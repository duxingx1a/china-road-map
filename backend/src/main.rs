use axum::{
    Router,
    body::Body,
    extract::{Path as AxumPath, Query, State},
    http::{HeaderMap, HeaderValue, Request, StatusCode, header},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::get,
};
use bytes::Bytes;
use flate2::{Compression, read::GzDecoder, write::GzEncoder};
use percent_encoding::percent_decode_str;
use rusqlite::{Connection, OpenFlags, OptionalExtension, params};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json, value::RawValue};
use std::{
    collections::{BTreeMap, HashMap, VecDeque},
    env,
    error::Error,
    io::{Read, Write},
    path::{Component, Path, PathBuf},
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::sync::{OwnedSemaphorePermit, Semaphore};
use tower_http::services::ServeDir;

const CSP: &str = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; worker-src 'self' blob:; font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'";
type AppResult<T> = Result<T, AppError>;
type TileKey = (String, u32, u32, u32);

#[derive(Debug)]
struct AppError(StatusCode, String);

impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        if self.0.is_server_error() {
            eprintln!("map-server: {}", self.1);
        }
        (
            self.0,
            axum::Json(json!({"error": if self.0.is_server_error() {
            "Resource read failed".to_owned()
        } else { self.1 }})),
        )
            .into_response()
    }
}

fn internal(error: impl std::fmt::Display) -> AppError {
    AppError(StatusCode::SERVICE_UNAVAILABLE, error.to_string())
}

// SQLite calls run on blocking workers, with a fixed pool and bounded page caches.
struct DbPool {
    available: Mutex<Vec<Connection>>,
    slots: Arc<Semaphore>,
}

struct DbLease {
    connection: Option<Connection>,
    pool: Arc<DbPool>,
    _permit: OwnedSemaphorePermit,
}

impl DbPool {
    fn open(path: &Path, size: usize) -> Result<Arc<Self>, Box<dyn Error>> {
        let mut available = Vec::with_capacity(size);
        for _ in 0..size {
            let connection = Connection::open_with_flags(
                path,
                OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
            )?;
            connection.busy_timeout(Duration::from_secs(2))?;
            connection.execute_batch(
                "PRAGMA query_only=ON; PRAGMA cache_size=-1024; PRAGMA mmap_size=0;",
            )?;
            connection.set_prepared_statement_cache_capacity(8);
            available.push(connection);
        }
        Ok(Arc::new(Self {
            available: Mutex::new(available),
            slots: Arc::new(Semaphore::new(size)),
        }))
    }

    async fn lease(self: &Arc<Self>) -> AppResult<DbLease> {
        let permit =
            tokio::time::timeout(Duration::from_secs(10), self.slots.clone().acquire_owned())
                .await
                .map_err(|_| internal("Database queue timed out"))?
                .map_err(internal)?;
        let connection = self
            .available
            .lock()
            .map_err(internal)?
            .pop()
            .ok_or_else(|| internal("Empty database pool"))?;
        Ok(DbLease {
            connection: Some(connection),
            pool: self.clone(),
            _permit: permit,
        })
    }
}

impl DbLease {
    fn db(&self) -> &Connection {
        self.connection.as_ref().expect("leased connection")
    }
}

impl Drop for DbLease {
    fn drop(&mut self) {
        if let Some(connection) = self.connection.take()
            && let Ok(mut available) = self.pool.available.lock()
        {
            available.push(connection);
        }
    }
}

// Compressed tile bytes are cheap to share. Both bytes and entry count are capped.
struct TileCache {
    entries: HashMap<TileKey, Bytes>,
    order: VecDeque<TileKey>,
    bytes: usize,
    limit: usize,
}

impl TileCache {
    fn new(limit: usize) -> Self {
        Self {
            entries: HashMap::new(),
            order: VecDeque::new(),
            bytes: 0,
            limit,
        }
    }
    fn get(&self, key: &TileKey) -> Option<Bytes> {
        self.entries.get(key).cloned()
    }
    fn insert(&mut self, key: TileKey, value: Bytes) {
        if value.len() > self.limit || self.entries.contains_key(&key) {
            return;
        }
        while self.bytes + value.len() > self.limit || self.entries.len() >= 4096 {
            if let Some(old) = self.order.pop_front() {
                if let Some(bytes) = self.entries.remove(&old) {
                    self.bytes -= bytes.len();
                }
            } else {
                break;
            }
        }
        self.bytes += value.len();
        self.order.push_back(key.clone());
        self.entries.insert(key, value);
    }
}

struct AppState {
    network: Option<Arc<DbPool>>,
    display_network: Option<Arc<DbPool>>,
    archives: HashMap<String, Arc<DbPool>>,
    compact_archives: HashMap<String, Arc<DbPool>>,
    public_mode: bool,
    tiles: Mutex<TileCache>,
    map_status: Bytes,
    health: Bytes,
}

impl AppState {
    fn load(data: &Path, cache_mb: usize) -> Result<Arc<Self>, Box<dyn Error>> {
        let mut archives = HashMap::new();
        let mut compact_archives = HashMap::new();
        let mut metadata = Vec::new();
        for (region, size) in [("china", 4), ("taiwan", 2)] {
            let path = data.join("basemap").join(format!("{region}.mbtiles"));
            let meta = path.with_extension("json");
            if path.is_file() && meta.is_file() {
                let mut value = serde_json::from_slice::<Value>(&std::fs::read(meta)?)?;
                let connection =
                    Connection::open_with_flags(&path, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
                if let Some(bounds) = connection
                    .query_row("SELECT value FROM metadata WHERE name='bounds'", [], |r| {
                        r.get::<_, String>(0)
                    })
                    .optional()?
                {
                    let parsed: Vec<f64> =
                        bounds.split(',').filter_map(|v| v.parse().ok()).collect();
                    if parsed.len() == 4 {
                        value["bounds"] = json!(parsed);
                    }
                }
                metadata.push(value);
                archives.insert(region.to_owned(), DbPool::open(&path, size)?);
                let compact = data.join("display").join(format!("{region}.mbtiles"));
                if compact.is_file() {
                    compact_archives.insert(region.to_owned(), DbPool::open(&compact, 2)?);
                }
            }
        }
        let network_path = data.join("network.sqlite");
        let network = if network_path.is_file() {
            Some(DbPool::open(&network_path, 2)?)
        } else {
            None
        };
        let display_path = data.join("display/network.sqlite");
        let display_network = if display_path.is_file() {
            Some(DbPool::open(&display_path, 2)?)
        } else {
            None
        };
        let public_mode = env::var("MAP_PUBLIC_MODE").is_ok_and(|v| v == "true");
        if public_mode && (display_network.is_none() || compact_archives.len() != 2) {
            return Err("Public display data is missing".into());
        }
        let map_status = Bytes::from(serde_json::to_vec(&json!({
            "basemapReady": !archives.is_empty(), "complete": archives.len() == 2,
            "archives": metadata, "preciseNetworkReady": network.is_some(),
        }))?);
        let report = data.join("report.json");
        let mut health = if report.is_file() {
            serde_json::from_slice::<Value>(&std::fs::read(&report)?)?
        } else {
            json!({})
        };
        health["ready"] = json!(report.is_file() && network.is_some() && archives.len() == 2);
        health["offline"] = json!(true);
        health["backend"] = json!("rust");
        health["version"] = json!(env!("CARGO_PKG_VERSION"));
        Ok(Arc::new(Self {
            network,
            display_network,
            archives,
            compact_archives,
            public_mode,
            tiles: Mutex::new(TileCache::new(cache_mb * 1024 * 1024)),
            map_status,
            health: Bytes::from(serde_json::to_vec(&health)?),
        }))
    }
}

fn accepts_gzip(headers: &HeaderMap) -> bool {
    headers
        .get(header::ACCEPT_ENCODING)
        .and_then(|v| v.to_str().ok())
        .is_some_and(|value| {
            value.split(',').any(|encoding| {
                let mut parts = encoding.trim().split(';');
                let name = parts.next().unwrap_or("").trim();
                (name.eq_ignore_ascii_case("gzip") || name == "*")
                    && !parts.any(|p| {
                        p.trim()
                            .strip_prefix("q=")
                            .and_then(|q| q.parse::<f32>().ok())
                            .is_some_and(|q| q <= 0.0)
                    })
            })
        })
}

fn response(bytes: Bytes, mime: &'static str, encoded: bool, cache: &'static str) -> Response {
    let length = bytes.len();
    let mut result = Response::new(Body::from(bytes));
    let headers = result.headers_mut();
    headers.insert(header::CONTENT_TYPE, HeaderValue::from_static(mime));
    headers.insert(header::CONTENT_LENGTH, HeaderValue::from(length));
    headers.insert(header::CACHE_CONTROL, HeaderValue::from_static(cache));
    headers.insert(header::VARY, HeaderValue::from_static("Accept-Encoding"));
    if encoded {
        headers.insert(header::CONTENT_ENCODING, HeaderValue::from_static("gzip"));
    }
    result
}

fn json_response(bytes: Bytes) -> Response {
    response(bytes, "application/json; charset=utf-8", false, "no-cache")
}

async fn map_status(State(state): State<Arc<AppState>>) -> Response {
    json_response(state.map_status.clone())
}
async fn health(State(state): State<Arc<AppState>>) -> Response {
    let ready = serde_json::from_slice::<Value>(&state.health)
        .ok()
        .is_some_and(|v| v["ready"] == true);
    let mut response = json_response(state.health.clone());
    if !ready {
        *response.status_mut() = StatusCode::SERVICE_UNAVAILABLE;
    }
    response
}

fn validate_tile(region: &str, z: u32, x: u32, y: u32) -> AppResult<()> {
    if !matches!(region, "china" | "taiwan") || z > 14 || x >= (1 << z) || y >= (1 << z) {
        Err(AppError(
            StatusCode::BAD_REQUEST,
            "Invalid tile coordinates".into(),
        ))
    } else {
        Ok(())
    }
}

async fn tile(
    State(state): State<Arc<AppState>>,
    AxumPath((region, z, x, file)): AxumPath<(String, u32, u32, String)>,
    headers: HeaderMap,
) -> AppResult<Response> {
    let y = file
        .strip_suffix(".pbf")
        .and_then(|y| y.parse::<u32>().ok())
        .ok_or_else(|| AppError(StatusCode::BAD_REQUEST, "Invalid tile name".into()))?;
    validate_tile(&region, z, x, y)?;
    let key = (region.clone(), z, x, y);
    let cached = state.tiles.lock().map_err(internal)?.get(&key);
    let data = if let Some(bytes) = cached {
        bytes
    } else if let Some(pool) = if z <= 11 {
        state
            .compact_archives
            .get(&region)
            .or_else(|| state.archives.get(&region))
    } else {
        state.archives.get(&region)
    } {
        let lease = pool.lease().await?;
        let bytes = tokio::task::spawn_blocking(move || -> AppResult<Bytes> {
            let mut statement = lease.db().prepare_cached("SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?").map_err(internal)?;
            let data = statement.query_row(params![z, x, (1 << z) - 1 - y], |row| row.get::<_, Vec<u8>>(0)).optional().map_err(internal)?.unwrap_or_default();
            Ok(Bytes::from(data))
        }).await.map_err(internal)??;
        state
            .tiles
            .lock()
            .map_err(internal)?
            .insert(key, bytes.clone());
        bytes
    } else {
        Bytes::new()
    };
    let encoded = data.starts_with(&[0x1f, 0x8b]);
    if encoded && !accepts_gzip(&headers) {
        let data = tokio::task::spawn_blocking(move || -> AppResult<Bytes> {
            let mut output = Vec::new();
            GzDecoder::new(data.as_ref())
                .take(16 * 1024 * 1024)
                .read_to_end(&mut output)
                .map_err(internal)?;
            Ok(Bytes::from(output))
        })
        .await
        .map_err(internal)??;
        return Ok(response(
            data,
            "application/vnd.mapbox-vector-tile",
            false,
            "public, max-age=2592000",
        ));
    }
    Ok(response(
        data,
        "application/vnd.mapbox-vector-tile",
        encoded,
        "public, max-age=2592000",
    ))
}

#[derive(Deserialize)]
struct NetworkQuery {
    bbox: String,
    kinds: Option<String>,
    zoom: Option<u8>,
}

fn bounds(value: &str) -> AppResult<[f64; 4]> {
    let parsed: Vec<f64> = value
        .split(',')
        .map(str::parse)
        .collect::<Result<_, _>>()
        .map_err(|_| AppError(StatusCode::BAD_REQUEST, "Invalid bounding box".into()))?;
    let valid = parsed.len() == 4
        && parsed.iter().all(|n| n.is_finite())
        && -180.0 <= parsed[0]
        && parsed[0] < parsed[2]
        && parsed[2] <= 180.0
        && -85.0 <= parsed[1]
        && parsed[1] < parsed[3]
        && parsed[3] <= 85.0;
    if !valid {
        return Err(AppError(
            StatusCode::BAD_REQUEST,
            "Invalid bounding box".into(),
        ));
    }
    Ok([parsed[0], parsed[1], parsed[2], parsed[3]])
}

fn kinds(value: &str) -> AppResult<Vec<String>> {
    let mut result = Vec::new();
    for kind in value.split(',') {
        if !matches!(kind, "national" | "expressway" | "provincial") {
            return Err(AppError(
                StatusCode::BAD_REQUEST,
                "Invalid road kinds".into(),
            ));
        }
        if !result.iter().any(|k| k == kind) {
            result.push(kind.to_owned());
        }
    }
    Ok(result)
}

#[derive(Serialize)]
struct Properties {
    id: String,
    code: String,
    kind: String,
}
#[derive(Serialize)]
struct Geometry {
    r#type: &'static str,
    coordinates: Vec<Box<RawValue>>,
}
#[derive(Serialize)]
struct Feature {
    r#type: &'static str,
    properties: Properties,
    geometry: Geometry,
}
#[derive(Serialize)]
struct Network {
    r#type: &'static str,
    features: Vec<Feature>,
    truncated: bool,
    ready: bool,
}

fn query_network_lod(
    connection: &Connection,
    bbox: [f64; 4],
    types: &[String],
    zoom: Option<u8>,
) -> AppResult<Vec<u8>> {
    let column = match zoom {
        Some(0..=8) => "z7",
        Some(9..=11) => "z10",
        _ => "coordinates",
    };
    let sql = format!(
        "SELECT s.route,s.code,s.kind,s.{column} FROM spatial r JOIN segments s ON s.id=r.id WHERE r.minx<=?1 AND r.maxx>=?2 AND r.miny<=?3 AND r.maxy>=?4 AND s.kind IN (?5,?6,?7) LIMIT 20001"
    );
    let mut statement = connection.prepare_cached(&sql).map_err(internal)?;
    let mut rows = statement
        .query(params![
            bbox[2],
            bbox[0],
            bbox[3],
            bbox[1],
            types.first(),
            types.get(1),
            types.get(2)
        ])
        .map_err(internal)?;
    let mut grouped: BTreeMap<String, Feature> = BTreeMap::new();
    let mut count = 0;
    let mut truncated = false;
    while let Some(row) = rows.next().map_err(internal)? {
        if count == 20000 {
            truncated = true;
            break;
        }
        count += 1;
        let id: String = row.get(0).map_err(internal)?;
        let code: String = row.get(1).map_err(internal)?;
        let kind: String = row.get(2).map_err(internal)?;
        let coordinates: String = row.get(3).map_err(internal)?;
        let coordinates = RawValue::from_string(coordinates).map_err(internal)?;
        grouped
            .entry(id.clone())
            .or_insert_with(|| Feature {
                r#type: "Feature",
                properties: Properties { id, code, kind },
                geometry: Geometry {
                    r#type: "MultiLineString",
                    coordinates: Vec::new(),
                },
            })
            .geometry
            .coordinates
            .push(coordinates);
    }
    serde_json::to_vec(&Network {
        r#type: "FeatureCollection",
        features: grouped.into_values().collect(),
        truncated,
        ready: true,
    })
    .map_err(internal)
}

#[cfg(test)]
fn query_network(connection: &Connection, bbox: [f64; 4], types: &[String]) -> AppResult<Vec<u8>> {
    query_network_lod(connection, bbox, types, None)
}

async fn network(
    State(state): State<Arc<AppState>>,
    Query(query): Query<NetworkQuery>,
    headers: HeaderMap,
) -> AppResult<Response> {
    let bbox = bounds(&query.bbox)?;
    if query.zoom.is_some_and(|z| !(7..=18).contains(&z)) {
        return Err(AppError(
            StatusCode::BAD_REQUEST,
            "Invalid display zoom".into(),
        ));
    }
    let zoom = if state.public_mode {
        Some(query.zoom.unwrap_or(7))
    } else {
        query.zoom
    };
    if state.public_mode {
        let side = 360.0 / 2.0_f64.powi(i32::from(zoom.unwrap_or(7)).min(14)) * 16.0;
        if bbox[2] - bbox[0] > side || bbox[3] - bbox[1] > side {
            return Err(AppError(
                StatusCode::BAD_REQUEST,
                "Viewport is too large for this zoom".into(),
            ));
        }
    }
    let types = kinds(
        query
            .kinds
            .as_deref()
            .unwrap_or("national,expressway,provincial"),
    )?;
    let selected_pool = if zoom.is_some() {
        state.display_network.as_ref().or(state.network.as_ref())
    } else {
        state.network.as_ref()
    };
    let Some(pool) = selected_pool else {
        return Ok(json_response(Bytes::from_static(
            b"{\"type\":\"FeatureCollection\",\"features\":[],\"ready\":false}",
        )));
    };
    let gzip = accepts_gzip(&headers);
    let lease = pool.lease().await?;
    let (data, encoded) = tokio::task::spawn_blocking(move || -> AppResult<(Bytes, bool)> {
        let raw = query_network_lod(
            lease.db(),
            bbox,
            &types,
            zoom.filter(|_| state.display_network.is_some()),
        )?;
        if gzip && raw.len() > 1024 {
            let mut encoder = GzEncoder::new(Vec::new(), Compression::new(6));
            encoder.write_all(&raw).map_err(internal)?;
            Ok((Bytes::from(encoder.finish().map_err(internal)?), true))
        } else {
            Ok((Bytes::from(raw), false))
        }
    })
    .await
    .map_err(internal)??;
    Ok(response(
        data,
        "application/json; charset=utf-8",
        encoded,
        "public, max-age=300",
    ))
}

fn allowed_data_path(uri: &str) -> bool {
    let decoded = match percent_decode_str(uri).decode_utf8() {
        Ok(value) => value,
        Err(_) => return false,
    };
    if decoded.contains('\\') || decoded.contains('\0') {
        return false;
    }
    let relative = decoded.strip_prefix("/data/").unwrap_or(&decoded);
    let path = Path::new(relative.strip_prefix('/').unwrap_or(relative));
    if path
        .components()
        .any(|part| !matches!(part, Component::Normal(_)))
    {
        return false;
    }
    let first = path
        .components()
        .next()
        .and_then(|part| part.as_os_str().to_str());
    if matches!(first, Some("raw" | "quality")) {
        return false;
    }
    !matches!(
        path.extension().and_then(|value| value.to_str()),
        Some("sqlite" | "mbtiles" | "gz" | "br" | "zip" | "pbf")
    )
}

async fn data_guard(
    State(state): State<Arc<AppState>>,
    request: Request<Body>,
    next: Next,
) -> Response {
    let path = request.uri().path();
    let decoded = percent_decode_str(path).decode_utf8_lossy();
    let relative = decoded
        .strip_prefix("/data/")
        .unwrap_or(&decoded)
        .trim_start_matches('/');
    let public_allowed = relative.starts_with("display/")
        || matches!(
            relative,
            "catalog.json"
                | "china.geojson"
                | "outside-mask.geojson"
                | "cities.geojson"
                | "maritime.geojson"
                | "sources.json"
        );
    if !allowed_data_path(path) || (state.public_mode && !public_allowed) {
        return AppError(StatusCode::FORBIDDEN, "Private data file".into()).into_response();
    }
    next.run(request).await
}

async fn headers(request: Request<Body>, next: Next) -> Response {
    let path = request.uri().path().to_owned();
    let mut result = next.run(request).await;
    let headers = result.headers_mut();
    headers.insert(
        header::CONTENT_SECURITY_POLICY,
        HeaderValue::from_static(CSP),
    );
    headers.insert(
        header::X_CONTENT_TYPE_OPTIONS,
        HeaderValue::from_static("nosniff"),
    );
    if !headers.contains_key(header::CACHE_CONTROL) {
        let cache = if path.starts_with("/data/") {
            "public, max-age=2592000"
        } else {
            "no-cache"
        };
        headers.insert(header::CACHE_CONTROL, HeaderValue::from_static(cache));
    }
    if path.ends_with(".geojson") {
        headers.insert(
            header::CONTENT_TYPE,
            HeaderValue::from_static("application/geo+json; charset=utf-8"),
        );
    }
    result
}

fn app(state: Arc<AppState>, web: PathBuf, data: PathBuf) -> Router {
    let data_files = Router::new()
        .fallback_service(
            ServeDir::new(data)
                .precompressed_gzip()
                .append_index_html_on_directories(false),
        )
        .layer(middleware::from_fn_with_state(state.clone(), data_guard));
    Router::new()
        .route("/api/health", get(health))
        .route("/api/map-status", get(map_status))
        .route("/api/network", get(network))
        .route("/tiles/{region}/{z}/{x}/{file}", get(tile))
        .nest("/data", data_files)
        .fallback_service(ServeDir::new(web).precompressed_gzip())
        .layer(middleware::from_fn(headers))
        .with_state(state)
}

async fn shutdown() {
    let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
        .expect("SIGTERM handler");
    tokio::select! { _ = tokio::signal::ctrl_c() => {}, _ = term.recv() => {} }
}

fn main() -> Result<(), Box<dyn Error>> {
    if env::args().nth(1).as_deref() == Some("--healthcheck") {
        let mut address: std::net::SocketAddr = env::var("MAP_LISTEN")
            .unwrap_or_else(|_| "127.0.0.1:8766".into())
            .parse()?;
        if address.ip().is_unspecified() {
            address.set_ip(std::net::Ipv4Addr::LOCALHOST.into());
        }
        let mut stream = std::net::TcpStream::connect_timeout(&address, Duration::from_secs(3))?;
        stream.set_read_timeout(Some(Duration::from_secs(3)))?;
        stream.write_all(
            b"GET /api/health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
        )?;
        let mut result = String::new();
        stream.take(16 * 1024).read_to_string(&mut result)?;
        if !result.starts_with("HTTP/1.1 200 ") {
            return Err("Map server is not ready".into());
        }
        return Ok(());
    }
    let workers: usize = env::var("MAP_WORKERS")
        .unwrap_or_else(|_| "2".into())
        .parse()?;
    tokio::runtime::Builder::new_multi_thread()
        .worker_threads(workers.clamp(1, 16))
        .max_blocking_threads(12)
        .enable_all()
        .build()?
        .block_on(async {
            let data = PathBuf::from(env::var("MAP_DATA_DIR").unwrap_or_else(|_| "data".into()));
            let web = PathBuf::from(env::var("MAP_WEB_DIR").unwrap_or_else(|_| "web".into()));
            let address = env::var("MAP_LISTEN").unwrap_or_else(|_| "127.0.0.1:8766".into());
            let cache_mb: usize = env::var("MAP_TILE_CACHE_MB")
                .unwrap_or_else(|_| "16".into())
                .parse()?;
            if !web.join("index.html").is_file() {
                return Err("Missing web/index.html".into());
            }
            let state = AppState::load(&data, cache_mb.min(128))?;
            let listener = tokio::net::TcpListener::bind(&address).await?;
            println!(
                "China Road Atlas / Rust {} listening on {address}",
                env!("CARGO_PKG_VERSION")
            );
            axum::serve(listener, app(state, web, data))
                .with_graceful_shutdown(shutdown())
                .await?;
            Ok(())
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    use http_body_util::BodyExt;
    use tower::ServiceExt;

    #[test]
    fn validates_bounds_and_kinds() {
        assert!(bounds("103.85,30.48,104.25,30.83").is_ok());
        for value in [
            "NaN,0,1,2",
            "104,31,103,30",
            "0,0,181,1",
            "1,2,3",
            "0,0,1,inf",
        ] {
            assert!(bounds(value).is_err());
        }
        assert_eq!(
            kinds("national,national,expressway").unwrap(),
            ["national", "expressway"]
        );
        for value in ["", "invalid", "national,", "national,road"] {
            assert!(kinds(value).is_err());
        }
    }

    #[test]
    fn validates_tiles_and_encoding_negotiation() {
        assert!(validate_tile("china", 14, 8192, 4096).is_ok());
        for (r, z, x, y) in [
            ("foreign", 1, 0, 0),
            ("china", 15, 0, 0),
            ("china", 1, 2, 0),
            ("china", 1, 0, 2),
        ] {
            assert!(validate_tile(r, z, x, y).is_err());
        }
        let mut headers = HeaderMap::new();
        for (value, expected) in [
            ("gzip, br", true),
            ("gzip;q=0", false),
            ("br", false),
            ("gzip;q=0.5", true),
        ] {
            headers.insert(header::ACCEPT_ENCODING, HeaderValue::from_static(value));
            assert_eq!(accepts_gzip(&headers), expected);
        }
    }

    #[test]
    fn blocks_private_files_and_encoded_traversal() {
        for path in [
            "/data/catalog.json",
            "/data/routes/national-G248.geojson",
            "/catalog.json",
        ] {
            assert!(allowed_data_path(path), "{path}");
        }
        for path in [
            "/data/raw/test.pdf",
            "/data/quality/test.geojson",
            "/data/network.sqlite",
            "/data/basemap/china.mbtiles",
            "/data/%2e%2e/server.py",
            "/data/raw%2ftest.pdf",
            "/data/a%5c..%5cb",
            "/data/catalog.json.gz",
        ] {
            assert!(!allowed_data_path(path), "{path}");
        }
    }

    #[test]
    fn tile_cache_is_bounded_and_does_not_duplicate_entries() {
        let mut cache = TileCache::new(6);
        let a = ("china".into(), 0, 0, 0);
        let b = ("china".into(), 1, 0, 0);
        let c = ("china".into(), 1, 1, 0);
        cache.insert(a.clone(), Bytes::from_static(b"123"));
        cache.insert(a.clone(), Bytes::from_static(b"123"));
        cache.insert(b.clone(), Bytes::from_static(b"456"));
        cache.insert(c, Bytes::from_static(b"789"));
        assert!(cache.get(&a).is_none());
        assert!(cache.get(&b).is_some());
        assert_eq!(cache.bytes, 6);
        assert_eq!(cache.entries.len(), 2);
    }

    #[test]
    fn spatial_query_preserves_original_coordinates_and_filters_kinds() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch("CREATE VIRTUAL TABLE spatial USING rtree(id,minx,maxx,miny,maxy); CREATE TABLE segments(id INTEGER,route TEXT,code TEXT,kind TEXT,coordinates TEXT); INSERT INTO spatial VALUES(1,103,105,30,31),(2,103,105,30,31),(3,120,121,40,41); INSERT INTO segments VALUES(1,'national-G248','G248','national','[[104.001,30.002],[104.003,30.004]]'),(2,'expressway-G5','G5','expressway','[[104,30],[105,31]]'),(3,'national-G1','G1','national','[[120,40],[121,41]]');").unwrap();
        let data: Value = serde_json::from_slice(
            &query_network(&conn, [103.8, 30.4, 104.3, 30.9], &["national".into()]).unwrap(),
        )
        .unwrap();
        assert_eq!(data["features"].as_array().unwrap().len(), 1);
        assert_eq!(
            data["features"][0]["geometry"]["coordinates"][0],
            json!([[104.001, 30.002], [104.003, 30.004]])
        );
        assert_eq!(data["truncated"], false);
    }

    #[tokio::test]
    async fn serves_static_files_and_denies_database_downloads() {
        let dir = tempfile::tempdir().unwrap();
        let web = dir.path().join("web");
        let data = dir.path().join("data");
        std::fs::create_dir_all(&web).unwrap();
        std::fs::create_dir_all(&data).unwrap();
        std::fs::write(web.join("index.html"), "map").unwrap();
        std::fs::write(data.join("catalog.json"), "[]").unwrap();
        std::fs::write(data.join("network.sqlite"), "private").unwrap();
        let state = Arc::new(AppState {
            network: None,
            display_network: None,
            archives: HashMap::new(),
            compact_archives: HashMap::new(),
            public_mode: true,
            tiles: Mutex::new(TileCache::new(0)),
            map_status: Bytes::from_static(b"{}"),
            health: Bytes::from_static(b"{\"ready\":false}"),
        });
        let router = app(state, web, data);
        for (path, status) in [
            ("/", 200),
            ("/data/catalog.json", 200),
            ("/data/network.sqlite", 403),
            ("/data/routes/national-G248.geojson", 403),
            ("/data/%72outes/national-G248.geojson", 403),
            ("/data/display/../routes/national-G248.geojson", 403),
            ("/data/%2e%2e/index.html", 403),
            ("/missing.js", 404),
        ] {
            let result = router
                .clone()
                .oneshot(Request::builder().uri(path).body(Body::empty()).unwrap())
                .await
                .unwrap();
            assert_eq!(result.status().as_u16(), status, "{path}");
            assert!(
                result
                    .headers()
                    .contains_key(header::CONTENT_SECURITY_POLICY)
            );
            result.into_body().collect().await.unwrap();
        }
    }
}

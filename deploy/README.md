# Rust 展示服务部署

生产访问地址：`https://ydl66.top/map/`。

当前镜像：`china-road-map:1.1.2`。含铁路的运行数据实测 4,008,672,414 字节，其中展示数据 775,908,367 字节；镜像在服务器上为 5,225,316 字节。

所有编译和镜像构建在本机 **Ubuntu-22.04 WSL** 完成。服务器只执行镜像导入、数据校验、解压和 Docker Compose 启动，不安装 Rust、编译器、Python 或 Node。

## 构建

```powershell
wsl -d Ubuntu-22.04 -- bash /mnt/e/projects/map/scripts/build_rust_image.sh
```

首次需先生成依赖锁文件：

```bash
cargo generate-lockfile --manifest-path /mnt/e/projects/map/backend/Cargo.toml
```

构建脚本使用 musl 静态链接、release 优化、Thin LTO 和去除调试符号。镜像以 `scratch` 为基础，只包含 `/map-server` 和 `/web`，数据独立挂载为 `/data`。

WSL 构建产物：

- `/home/ydl/map-build/image/map-server`：Linux x86_64 静态程序。
- `/home/ydl/map-build/artifacts/map-image.tar.gz`：成品镜像。
- `/home/ydl/map-build/artifacts/map-data.tar.gz`：运行所需原始数据包。
- `/home/ydl/map-build/artifacts/map-sidecars.tar`：提前生成的 gzip 静态数据副本。

运行数据包含底图、空间索引、路线、目录、边界、地名和来源说明。原始下载文件、核对数据库和历史归档保留在本机，不复制进展示服务。

## 服务器目录

```text
/root/ydl-projects/map/
  compose.yaml
  data/
  deployment.json
```

Nginx location 配置为 `/root/ydl-projects/nginx/config/conf.d/locations/map.conf`，接入已有 `nginx_nginx-net` Docker 网络。容器没有额外公开端口，`/map` 重定向至 `/map/`，代理时去掉 `/map/` 前缀。前端所有请求按当前页面目录生成，根路径运行仍然兼容。

`deploy/map-limits.nginx.conf` 须安装为 `/root/ydl-projects/nginx/config/conf.d/map-limits.conf`，由 Nginx 的 http 层加载，不能放入 locations 目录。地图 location 引用这些限流区；其他项目不受影响。

```bash
# 仅导入本机已经构建好的镜像。
gzip -dc map-image.tar.gz | docker load
docker compose -f /root/ydl-projects/map/compose.yaml up -d --no-build
docker exec china-road-map /map-server --healthcheck
docker exec nginx-proxy nginx -t
docker exec nginx-proxy nginx -s reload
```

## 运行资源

- 容器内存上限 256MiB，CPU 上限 1.25 核。
- SQLite 只读连接池：大陆底图 4 个、台湾底图 2 个、精细路网 2 个。
- 每个连接的 SQLite 页面缓存目标 1MiB，不启用整库 mmap。
- 压缩瓦片缓存上限 16MiB / 4096 项。
- 静态文件以流式响应提供，优先使用提前生成的 gzip 副本，支持 HEAD 与条件请求。
- 视野路网查询保留原始坐标，最多返回 20000 段；压缩在阻塞工作线程执行。
- 非 root 运行、数据只读、容器根文件系统只读，日志最多 2 × 5MiB。
- 容器每 30 秒使用自身程序检查健康状态，不额外装 curl 或 shell。
- Docker 重启后自动启动；程序收到 SIGTERM 时等待现有请求结束。

公路跨海段更新须同步原始路线、三档展示路线、概览、目录和两套空间索引；避免列表、选中走向和背景路网使用不同版本。原始桥隧证据与下载源只保留在本机。

铁路更新包只包含目录、运行索引、铁路概览和分级路线，不包含源 PBF 或铁路核对数据库。首次启用铁路背景才下载约 584KB 的 gzip 概览，两类查询按 `highspeed` / `railway` 分离。

数据更新须先停止地图容器，再替换整套匹配的底图/索引/路线，完成后启动容器。网页和地图块缓存最长一小时，修改现有数据后客户端可能需要刷新或清理缓存。

## 验证

```powershell
node --test tests/test_view_controls.cjs tests/test_resource_paths.cjs tests/test_route_loader.cjs tests/test_tile_loader.cjs
.\.prepare-venv\Scripts\python.exe tests/test_display_data.py
python tests/verify_rust_server.py --base http://127.0.0.1:18766 --baseline http://127.0.0.1:18768 --public
python tests/verify_rust_server.py --base https://ydl66.top/map/ --public
python tests/verify_railway_server.py --base https://ydl66.top/map/ --output verification-railways-server.json
```

验证包含真实底图块、展示路线、分类查询、gzip、HEAD、路径保护，以及 Python 与 Rust 展示坐标一致性。原始坐标保留测试仍在 Rust 单元测试中。当前公开模式的功能检查与限流压力检查分开进行，避免将有意拦截的批量请求误报为服务故障。验收记录为 `verification-rust-local.json`、`verification-rust-server.json`、`verification-public-safety.json`、`verification-display-payloads.json`。

路线列表和地图道路悬停约 160ms 后预加载。最多同时读取两条路线，点击与悬停复用同一个请求，点击优先于后台预加载。浏览器缓存最多 12 条路线，按 JSON 文本大小估算限额 8MiB；这不是解析后 JavaScript 对象的内存硬限制。加载指示持续到选中道路的数据完成地图源处理。

## 展示数据与低带宽

在本机使用已有准备环境运行 `scripts/build_display_data.py`，只生成 `data/display/`，不修改原始文件。依赖见 `requirements-basemap.txt`。路线分三级：远景 200m、中景 10m、近景 0.4m 简化阈值，坐标按 5/6 位小数输出；保留各独立线段，禁止跨断点连接。比例尺变化后自动切换，地图视野不改变。近景精度测试包含坐标取整误差。

底图 z<=11 的展示副本去掉未显示图层、字段和低比例尺不需要的道路，按 0.6 像素阈值简化；更近的视野读取原来的底图块。浏览器瓦片最多同时下载 3 个，等待中的过期请求在发出前移除，缓存上限 12MiB / 160 块。台湾源使用实际 bounds，避免在大陆视野请求大量空白台湾瓦片。静态数据和瓦片缓存 30 天，展示 URL 带版本参数；更新展示数据时同步更新 `DISPLAY_VERSION`，不可在不换版本的情况下修改已缓存内容。

## 公开保护的边界

生产设置 `MAP_PUBLIC_MODE=true`。完整原始路线、数据库、内部报告与原始下载均不通过 HTTP 提供，只允许展示文件及必要目录/地名/边界。前端取消原始 GeoJSON 导出。精细路网按 zoom 选择预生成的展示坐标，并限制过大的查询视野。

Nginx 按 IP 分别限制：瓦片 20 次/秒 + 80 次突发，路线展示文件 60 次/分钟 + 40 次突发，视野路网 2 次/秒 + 10 次突发。超过后返回 429 与 Retry-After: 2；客户端对暂时错误重试。共享出口 IP 的多人访问和持续快速操作仍可能触发限制，这些阈值不能视为完全无影响或不可绕过。

公开展示的坐标仍可被保存、转换和拼接。加密传输（HTTPS）保护链路，磁盘加密保护磁盘泄露，都不能阻止合法网页访问者从浏览器提取已解密的绘图数据。当前保护的是原始下载入口并提高批量抓取成本，未承诺“爬到后不可用”。如需更强保护，可改用服务端图片展示，但仍能截图，并需权衡交互、算力、存储和带宽成本。

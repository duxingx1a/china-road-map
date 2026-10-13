# The binary is compiled in local WSL before this image is assembled.
FROM scratch
COPY map-server /map-server
COPY web/ /web/
ENV MAP_LISTEN=0.0.0.0:8080 MAP_DATA_DIR=/data MAP_WEB_DIR=/web MAP_TILE_CACHE_MB=16 MAP_WORKERS=2
USER 65532:65532
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD ["/map-server", "--healthcheck"]
ENTRYPOINT ["/map-server"]

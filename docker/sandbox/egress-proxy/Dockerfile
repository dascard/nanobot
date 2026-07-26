FROM ubuntu/squid:6.6-24.04_beta@sha256:6a097f68bae708cedbabd6188d68c7e2e7a38cedd05a176e1cc0ba29e3bbe029

LABEL org.opencontainers.image.title="Nanobot Sandbox Egress Proxy" \
      org.opencontainers.image.description="固定域名白名单的 Sandbox CONNECT 出口代理" \
      org.opencontainers.image.version="2026.07.25" \
      com.nanobot.egress-policy-id="developer_allowlist_v1"

COPY squid.conf /etc/squid/squid.conf

USER 13:13

ENTRYPOINT ["/usr/sbin/squid"]
CMD ["-N", "-f", "/etc/squid/squid.conf"]

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["/bin/sh", "-c", "test \"$(cat /proc/1/comm)\" = squid"]

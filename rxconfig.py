# rxconfig.py
import reflex as rx
from reflex.plugins import RadixThemesPlugin, SitemapPlugin

config = rx.Config(
    app_name="app",
    telemetry_enabled=False,
    plugins=[RadixThemesPlugin(), SitemapPlugin()],
)
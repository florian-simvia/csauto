from typing import Any


def register_settings_routes(app: Any, ctx: Any, components: dict[str, Any]) -> None:
    BaseModel = components["BaseModel"]
    Header = components["Header"]

    class TelemetrySettings(BaseModel):
        enabled: bool

    @app.get("/api/settings/telemetry", response_model=TelemetrySettings)
    def get_telemetry_settings(
        x_csauto_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        ctx.require_auth(x_csauto_token, authorization)
        from ..telemetry import is_enabled

        return {"enabled": is_enabled()}

    @app.post("/api/settings/telemetry", response_model=TelemetrySettings)
    def set_telemetry_settings(
        payload: TelemetrySettings,
        x_csauto_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        ctx.require_auth(x_csauto_token, authorization)
        from ..telemetry import is_enabled, set_enabled

        set_enabled(payload.enabled)
        return {"enabled": is_enabled()}

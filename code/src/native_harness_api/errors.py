"""Bounded public failures: never include exception, path, or credential text."""
class ChannelError(Exception):
    def __init__(self, code: str, status: int = 502, *, accepted: bool | None = None, param: str | None = None):
        self.code, self.status, self.accepted = code, status, accepted
        self.param = param if isinstance(param, str) and len(param) <= 64 and param.replace("_", "").isalnum() else None
        super().__init__(code)

    def body(self):
        value = {"error": {"message": self.code, "type": "native_channel_error", "code": self.code}}
        if self.param is not None:
            value["error"]["param"] = self.param
        return value

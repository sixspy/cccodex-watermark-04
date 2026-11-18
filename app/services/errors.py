class ServiceError(Exception):
    def __init__(self, message: str, status_code: int = 400, extra: dict | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        # 冲突收束时带回保留/作废凭证信息，供前端亮芯片
        self.extra = extra or {}

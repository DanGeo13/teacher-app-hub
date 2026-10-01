class NotFoundError(LookupError):
    pass


class ConflictError(RuntimeError):
    pass


class ApprovalError(RuntimeError):
    pass

class SecretBroker:
    def __init__(self, load_secret):
        self._load_secret = load_secret

    def invoke(self, source_id, operation):
        try:
            secret = self._load_secret(source_id)
            if isinstance(secret, str) and secret:
                result = operation(secret)
                if type(result) is bool:
                    return result
                if type(result) is str and secret not in result:
                    return result
        except Exception:
            # Keep loader and callback failures out of the caller's error.
            pass

        raise ValueError("source operation refused")

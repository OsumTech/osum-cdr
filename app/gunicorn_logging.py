from gunicorn.glogging import Logger


class RedactedAccessLogger(Logger):
    def access(self, resp, req, environ, request_time):
        if environ.get('PATH_INFO', '').startswith('/webhooks/didlogic/'):
            return
        return super().access(resp, req, environ, request_time)

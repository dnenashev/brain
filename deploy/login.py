from http.cookies import CookieError, SimpleCookie
from secrets import compare_digest
from urllib.parse import parse_qs


_FORM = b'''<!doctype html>
<meta charset="utf-8">
<title>Review sign in</title>
<form method="post" action="/login">
  <label>Credential <input type="password" name="credential" required></label>
  <button type="submit">Sign in</button>
</form>
'''


def install_login(server, sessions):
    class LoginHandler(server.RequestHandlerClass):
        def _is_login(self):
            return self.path.partition("?")[0] == "/login"

        def log_request(self, code="-", size="-"):
            if not self._is_login():
                super().log_request(code, size)

        def _login_response(self, status, body=b"", **headers):
            self.send_response(status)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            for name, value in headers.items():
                self.send_header(name.replace("_", "-"), value)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self):
            if not self._is_login():
                return super().do_GET()
            self._login_response(200, _FORM, Content_Type="text/html; charset=utf-8")

        def do_POST(self):
            if self.path.partition("?")[0] == "/logout":
                return self._logout()
            if not self._is_login():
                return super().do_POST()
            token = None
            try:
                lengths = self.headers.get_all("Content-Length", [])
                if (len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdecimal()
                        or "Transfer-Encoding" in self.headers):
                    raise ValueError
                length = int(lengths[0])
                if not 0 < length <= 8192 or self.headers.get_content_type() != "application/x-www-form-urlencoded":
                    raise ValueError
                data = self.rfile.read(length)
                if len(data) != length:
                    raise ValueError
                fields = parse_qs(data.decode("ascii"), errors="strict", max_num_fields=8)
                credentials = fields.get("credential", [])
                if len(credentials) == 1:
                    token = sessions.open(credentials[0])
            except (ValueError, OSError):
                pass
            if token is None:
                self.close_connection = True
                return self._login_response(403, Connection="close")
            cookie = SimpleCookie()
            cookie["brain_session"] = token
            session = cookie["brain_session"]
            session["path"] = "/"
            session["httponly"] = True
            session["samesite"] = "Strict"
            self._login_response(303, Location="/", Set_Cookie=session.OutputString())

        def _logout(self):
            try:
                cookie = SimpleCookie("; ".join(self.headers.get_all("Cookie", [])))
                morsel = cookie.get("brain_session")
                token = morsel.value if morsel is not None else None
                expected = sessions.csrf(token)
                lengths = self.headers.get_all("Content-Length", [])
                if (len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdecimal()
                        or self.headers.get("Transfer-Encoding") is not None
                        or self.headers.get_content_type() != "application/x-www-form-urlencoded"):
                    raise ValueError
                length = int(lengths[0])
                if not 0 < length <= 1024:
                    raise ValueError
                fields = parse_qs(self.rfile.read(length).decode("ascii"), max_num_fields=2)
                values = fields.get("form_token", [])
                if expected is None or len(values) != 1 or not compare_digest(expected.encode(), values[0].encode()):
                    raise ValueError
            except (CookieError, ValueError, OSError):
                self.close_connection = True
                return self._login_response(403, Connection="close")
            sessions.revoke(token)
            self._login_response(303, Location="/login",
                                 Set_Cookie="brain_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")

    server.RequestHandlerClass = LoginHandler

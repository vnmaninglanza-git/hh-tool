import argparse
import logging
import platform
import socketserver
import subprocess
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ..api import OAuthClient
from ..constants import HHANDROID_SOCKET_PATH
from ..main import BaseOperation, Namespace
from ..utils import Config

logger = logging.getLogger(__package__)


class HHAndroidProtocolServer(socketserver.ThreadingUnixStreamServer):
    def __init__(
        self,
        socket_path: Path | str,
        oauth_client: OAuthClient,
        config: Config,
    ) -> None:
        self._socket_path = Path(socket_path)
        self._oauth_client = oauth_client
        self._config = config
        super().__init__(str(self._socket_path), HHAndroidProtocolHandler)

    def server_bind(self) -> None:
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._socket_path.unlink(missing_ok=True)
        return super().server_bind()

    def server_close(self) -> None:
        self._socket_path.unlink()
        return super().server_close()

    def handle_redirect_uri(self, redirect_uri: str) -> None:
        logger.debug(redirect_uri)
        sp = urlsplit(redirect_uri)
        assert sp.scheme == "hhandroid"
        assert sp.netloc == "oauthresponse"
        code = parse_qs(sp.query)["code"][0]
        token = self._oauth_client.authenticate(code)
        logger.debug("Сохраняем токен")
        # токен не содержит каких-то меток о времени создания
        self._config.save(token=dict(token, created_at=int(time.time())))
        print("🔓 Авторизация прошла успешно!")
        self.shutdown()


class HHAndroidProtocolHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.server.handle_redirect_uri(self.request.recv(1024).decode())


class Operation(BaseOperation):
    """Авторизоваться на сайте"""

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        pass

    def run(self, args: Namespace) -> None:
        oauth = OAuthClient(
            user_agent=(
                args.config["oauth_user_agent"] or args.config["user_agent"]
            ),
        )
        print("Пробуем открыть в браузере:", oauth.authorize_url)
        opener = "open" if platform.system() == "Darwin" else "xdg-open"
        try:
            subprocess.Popen([opener, oauth.authorize_url])
        except FileNotFoundError:
            print("Не удалось открыть браузер автоматически. Откройте ссылку вручную.")
        print("Авторизуйтесь и нажмите <Подтвердить>")
        logger.info(
            "🚀 Запускаем TCP-сервер и слушаем unix://%s", HHANDROID_SOCKET_PATH
        )
        server = HHAndroidProtocolServer(
            HHANDROID_SOCKET_PATH, oauth_client=oauth, config=args.config
        )
        server.serve_forever()

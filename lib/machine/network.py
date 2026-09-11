import json
import os
import shutil
import subprocess
import threading
import time
from .utils import get


class SpeedtestRunner:
    def __init__(self, interval: int = 3600, cache_file: str = "/tmp/speedtest_cache.json"):
        self.interval = interval
        self.cache_file = cache_file
        self.last_result = None
        self.is_running = False
        self._load_cache()
        self._start_background_thread()

    def _load_cache(self):
        # 1. Load from json cache file
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "download" in data:
                        self.last_result = data
            except Exception:
                pass

        # 2. Check legacy /tmp/speedtest (format: ping;down;up)
        if not self.last_result and os.path.exists("/tmp/speedtest"):
            try:
                with open("/tmp/speedtest", "r") as f:
                    content = f.read().strip()
                    parts = content.split(";")
                    if len(parts) == 3:
                        self.last_result = {
                            "ping": round(float(parts[0]), 1),
                            "download": round(float(parts[1]), 2),
                            "upload": round(float(parts[2]), 2),
                            "timestamp": time.time()
                        }
            except Exception:
                pass

    def _save_cache(self):
        if not self.last_result:
            return
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            with open(self.cache_file, "w") as f:
                json.dump(self.last_result, f)
        except Exception:
            pass

    def run_test(self):
        # 1. Prefer speedtest-cli binary (fast, isolated, reliable)
        speedtest_bin = shutil.which("speedtest-cli") or shutil.which("speedtest")
        if speedtest_bin:
            try:
                proc = subprocess.run(
                    [speedtest_bin, "--json"],
                    capture_output=True,
                    text=True,
                    timeout=90
                )
                if proc.returncode == 0 and proc.stdout:
                    data = json.loads(proc.stdout)
                    down = round(float(data.get("download", 0)) / 1e6, 2)
                    up = round(float(data.get("upload", 0)) / 1e6, 2)
                    ping = round(float(data.get("ping", 0)), 1)
                    result = {
                        "ping": ping,
                        "download": down,
                        "upload": up,
                        "timestamp": time.time()
                    }
                    self.last_result = result
                    self._save_cache()
                    return result
            except Exception:
                pass

        # 2. Fallback to python speedtest module
        try:
            import speedtest
            st = speedtest.Speedtest()
            st.get_servers()
            best = st.get_best_server()
            st.download()
            st.upload()
            res = st.results.dict()
            down = round(float(res.get("download", 0)) / 1e6, 2)
            up = round(float(res.get("upload", 0)) / 1e6, 2)
            ping = round(float(res.get("ping") or best.get("latency", 0)), 1)
            result = {
                "ping": ping,
                "download": down,
                "upload": up,
                "timestamp": time.time()
            }
            self.last_result = result
            self._save_cache()
            return result
        except Exception:
            pass

        return None

    def trigger(self):
        if not self.is_running:
            threading.Thread(target=self._run_wrapper, daemon=True).start()
            return True
        return False

    def _run_wrapper(self):
        self.is_running = True
        try:
            self.run_test()
        finally:
            self.is_running = False

    def _worker(self):
        time.sleep(1)
        while True:
            now = time.time()
            should_run = False
            if not self.last_result:
                should_run = True
            elif (now - self.last_result.get("timestamp", 0)) >= self.interval:
                should_run = True

            if should_run and not self.is_running:
                self._run_wrapper()

            time.sleep(60)

    def _start_background_thread(self):
        thread = threading.Thread(target=self._worker, daemon=True)
        thread.start()

    def get_result(self):
        return self.last_result

    def get_speed(self):
        if self.last_result:
            return f"Ping: {self.last_result['ping']} ms, Down: {self.last_result['download']} mbps, Up: {self.last_result['upload']} mbps"
        if self.is_running:
            return "Running speedtest..."
        return None


class Network:
    _speedtest_runner = None

    def __init__(self):
        if Network._speedtest_runner is None:
            Network._speedtest_runner = SpeedtestRunner()

    @classmethod
    def get_speedtest_runner(cls):
        if cls._speedtest_runner is None:
            cls._speedtest_runner = SpeedtestRunner()
        return cls._speedtest_runner

    def get_net(self):
        interface = get_default_iface_name_linux()
        path = f"/sys/class/net/{interface}" if interface else None

        rx = 0
        tx = 0
        link_speed = -1

        if path:
            try:
                rx = get(f"{path}/statistics/rx_bytes", isint=True)
                tx = get(f"{path}/statistics/tx_bytes", isint=True)
            except FileNotFoundError:
                rx = 0
                tx = 0

            try:
                val = get(f"{path}/speed", isint=True)
                if val is not None and val > 0:
                    link_speed = val
            except (OSError, FileNotFoundError, ValueError):
                link_speed = -1

        st_speed = self._speedtest_runner.get_speed()
        if st_speed is not None:
            speed = st_speed
        elif link_speed != -1:
            speed = link_speed
        else:
            speed = "Unknown"

        return {
            "interface": interface,
            "speed": speed,
            "speedtest": self._speedtest_runner.last_result,
            "rx": rx,
            "tx": tx
        }


def get_default_iface_name_linux():
    route = "/proc/net/route"
    interface = None
    if not os.path.exists(route):
        return None
    with open(route) as f:
        for line in f.readlines():
            try:
                parts = line.strip().split()
                if len(parts) < 4:
                    continue
                iface, dest, _, flags = parts[:4]
                if dest != '00000000' or not int(flags, 16) & 2:
                    continue
                interface = iface
                break
            except Exception:
                continue
    return interface

# Copyright (c) 2026 Ant Group Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Internal openYuanrong actor executed inside an AKernel sandbox."""

import yr


@yr.instance
class _SandboxInstance:
    """Remote sandbox instance running inside the container.

    All methods use local imports to survive yr serialization.
    Returns dicts for yr RPC serialization compatibility.
    """

    def __init__(self, cwd=None):
        import os
        import tempfile

        if cwd is not None:
            os.makedirs(cwd, exist_ok=True)
            self._cwd = cwd
        else:
            self._cwd = tempfile.mkdtemp(prefix="sandbox_")
        self._procs = {}

    # ── filesystem methods ─────────────────────────────────────────────

    def fs_read(self, path, binary=False):
        try:
            mode = "rb" if binary else "r"
            with open(path, mode) as f:
                data = f.read()
            if binary:
                return {"data": data.hex(), "error": None}
            return {"data": data, "error": None}
        except Exception as e:
            return {"data": None, "error": str(e)}

    def fs_write(self, path, data, binary=False):
        import os

        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            if binary:
                with open(path, "wb") as f:
                    f.write(bytes.fromhex(data))
            else:
                with open(path, "w") as f:
                    f.write(data)
            st = os.stat(path)
            return {
                "path": path,
                "name": os.path.basename(path),
                "type": "file",
                "size": st.st_size,
                "error": None,
            }
        except Exception as e:
            return {"path": path, "name": "", "type": "", "size": 0, "error": str(e)}

    def fs_list(self, path, depth=1):
        import os
        import stat as stat_mod

        def _scan(p, current_depth):
            entries = []
            try:
                for entry in os.scandir(p):
                    try:
                        st = entry.stat(follow_symlinks=False)
                        if entry.is_symlink():
                            etype = "symlink"
                        elif entry.is_dir(follow_symlinks=False):
                            etype = "dir"
                        else:
                            etype = "file"
                        entries.append(
                            {
                                "name": entry.name,
                                "path": entry.path,
                                "type": etype,
                                "size": st.st_size,
                                "permissions": stat_mod.filemode(st.st_mode)[1:],
                                "modified_time": st.st_mtime,
                            }
                        )
                        if etype == "dir" and current_depth < depth:
                            entries.extend(_scan(entry.path, current_depth + 1))
                    except OSError:
                        continue
            except OSError:
                pass
            return entries

        return {"entries": _scan(path, 1), "error": None}

    def fs_exists(self, path):
        import os

        return {"exists": os.path.exists(path)}

    def fs_remove(self, path):
        import os
        import shutil

        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            return {"error": None}
        except Exception as e:
            return {"error": str(e)}

    def fs_rename(self, old_path, new_path):
        import os
        import stat as stat_mod

        try:
            os.makedirs(os.path.dirname(new_path) or ".", exist_ok=True)
            os.rename(old_path, new_path)
            st = os.stat(new_path)
            if os.path.isdir(new_path):
                etype = "dir"
            elif os.path.islink(new_path):
                etype = "symlink"
            else:
                etype = "file"
            return {
                "name": os.path.basename(new_path),
                "path": new_path,
                "type": etype,
                "size": st.st_size,
                "permissions": stat_mod.filemode(st.st_mode)[1:],
                "modified_time": st.st_mtime,
                "error": None,
            }
        except Exception as e:
            return {"error": str(e)}

    def fs_make_dir(self, path):
        import os

        try:
            existed = os.path.exists(path)
            os.makedirs(path, exist_ok=True)
            return {"created": not existed, "error": None}
        except Exception as e:
            return {"created": False, "error": str(e)}

    def fs_get_info(self, path):
        import os
        import stat as stat_mod

        try:
            st = os.stat(path)
            if os.path.islink(path):
                etype = "symlink"
            elif os.path.isdir(path):
                etype = "dir"
            else:
                etype = "file"
            return {
                "name": os.path.basename(path),
                "path": path,
                "type": etype,
                "size": st.st_size,
                "permissions": stat_mod.filemode(st.st_mode)[1:],
                "modified_time": st.st_mtime,
                "error": None,
            }
        except Exception as e:
            return {"error": str(e)}

    # ── command execution methods ──────────────────────────────────────

    def _command_env(self, envs=None):
        import os

        env = os.environ.copy()
        python_path = env.get("PYTHONPATH")
        if python_path:
            paths = [
                path
                for path in python_path.split(os.pathsep)
                if not (
                    path.startswith("/__yuanrong/opt/venv-py")
                    and path.endswith("/site-packages")
                )
            ]
            if paths:
                env["PYTHONPATH"] = os.pathsep.join(paths)
            else:
                env.pop("PYTHONPATH", None)
        if envs:
            env.update(envs)
        return env

    def cmd_run(self, cmd, envs=None, cwd=None, timeout=60):
        import subprocess

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                cwd=cwd or self._cwd,
                env=self._command_env(envs),
                timeout=timeout,
                # Foreground one-shot commands cannot receive stdin (no handle
                # is returned), so detach stdin to /dev/null. Otherwise the
                # child inherits the runtime's stdin and any interactive
                # prompt (e.g. apt/debconf tzdata) blocks forever on read().
                stdin=subprocess.DEVNULL,
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
            }
        except subprocess.TimeoutExpired as e:
            return {
                "stdout": e.stdout.decode() if e.stdout else "",
                "stderr": f"Command timed out after {timeout} seconds",
                "exit_code": -1,
            }
        except Exception as e:
            return {"stdout": "", "stderr": str(e), "exit_code": -1}

    def cmd_start(self, cmd, envs=None, cwd=None, want_stdin=False):
        import subprocess
        import threading

        try:
            # Default stdin to /dev/null: an open PIPE with no writer never
            # reaches EOF, so any interactive prompt (apt/debconf tzdata, etc.)
            # blocks forever on read(). Callers that need send_stdin must opt
            # in via want_stdin=True.
            proc = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE if want_stdin else subprocess.DEVNULL,
                cwd=cwd or self._cwd,
                env=self._command_env(envs),
            )

            stdout_chunks: list[bytes] = []
            stderr_chunks: list[bytes] = []

            def _read_stream(stream, chunks):
                try:
                    while True:
                        data = stream.read(4096)
                        if not data:
                            break
                        chunks.append(data)
                except Exception:
                    pass

            stdout_thread = threading.Thread(
                target=_read_stream, args=(proc.stdout, stdout_chunks), daemon=True
            )
            stderr_thread = threading.Thread(
                target=_read_stream, args=(proc.stderr, stderr_chunks), daemon=True
            )
            stdout_thread.start()
            stderr_thread.start()

            self._procs[proc.pid] = {
                "proc": proc,
                "cmd": cmd,
                "stdout_chunks": stdout_chunks,
                "stderr_chunks": stderr_chunks,
                "stdout_thread": stdout_thread,
                "stderr_thread": stderr_thread,
            }
            return {"pid": proc.pid, "error": None}
        except Exception as e:
            return {"pid": -1, "error": str(e)}

    def _collect_proc_output(self, entry):
        """Wait for reader threads and return collected stdout/stderr."""
        entry["stdout_thread"].join(timeout=5)
        entry["stderr_thread"].join(timeout=5)
        stdout = b"".join(entry["stdout_chunks"]).decode("utf-8", errors="replace")
        stderr = b"".join(entry["stderr_chunks"]).decode("utf-8", errors="replace")
        proc = entry["proc"]
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except Exception:
                pass
        return stdout, stderr

    def cmd_wait(self, pid, timeout=None):
        try:
            entry = self._procs.get(pid)
            if entry is None:
                return {
                    "stdout": "",
                    "stderr": f"No process with pid {pid}",
                    "exit_code": -1,
                }
            proc = entry["proc"]
            try:
                proc.wait(timeout=timeout)
            except Exception as e:
                return {"stdout": "", "stderr": str(e), "exit_code": -1}
            stdout, stderr = self._collect_proc_output(entry)
            return {
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": proc.returncode,
            }
        except Exception as e:
            return {"stdout": "", "stderr": str(e), "exit_code": -1}

    def cmd_list(self):
        processes = []
        for pid, entry in self._procs.items():
            proc = entry["proc"]
            processes.append(
                {
                    "pid": pid,
                    "cmd": entry["cmd"],
                    "running": proc.poll() is None,
                }
            )
        return {"processes": processes}

    def cmd_kill(self, pid):
        try:
            entry = self._procs.get(pid)
            if entry is None:
                return {"killed": False, "error": f"No process with pid {pid}"}
            entry["proc"].kill()
            return {"killed": True, "error": None}
        except Exception as e:
            return {"killed": False, "error": str(e)}

    def cmd_send_stdin(self, pid, data, eof=False):
        try:
            entry = self._procs.get(pid)
            if entry is None:
                return {"error": f"No process with pid {pid}"}
            proc = entry["proc"]
            if proc.stdin is None:
                # stdin was detached to /dev/null (default). Fail loudly
                # instead of silently dropping the data.
                return {
                    "error": (
                        f"process {pid} was not started with stdin enabled; "
                        "start it with stdin=True to use send_stdin"
                    )
                }
            if proc.stdin.closed:
                return {"error": f"stdin of process {pid} is already closed"}
            if data:
                proc.stdin.write(data.encode())
                proc.stdin.flush()
            if eof:
                # Close the write end so the child sees EOF on its next
                # read(). Required for processes that only act on EOF
                # (cat, sort, wc, python3 -, ...).
                proc.stdin.close()
            return {"error": None}
        except Exception as e:
            return {"error": str(e)}

    # ── tunnel methods ────────────────────────────────────────────────

    def start_tunnel_server(self, ws_port=8765, http_port=8766):
        """Start TunnelServer in a background thread within this sandbox instance.

        Port A (ws_port): WebSocket endpoint for TunnelClient connection.
        Port B (http_port): HTTP proxy for sandbox code to call.
        """
        import asyncio
        import socket as _socket
        import threading
        import time

        from yr.sandbox.tunnel_server import TunnelServer

        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            server = TunnelServer(ws_port=ws_port, http_port=http_port)
            loop.run_until_complete(server.start())
            loop.run_forever()

        t = threading.Thread(target=_run, name="tunnel-server", daemon=True)
        t.start()
        # Wait until both ports are actually bound (up to 5s)
        deadline = time.time() + 5.0
        for port in (ws_port, http_port):
            while time.time() < deadline:
                try:
                    _socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
                    break
                except OSError:
                    time.sleep(0.1)
        return {"error": None}

    # ── lifecycle methods ──────────────────────────────────────────────

    def ping(self):
        return {"status": "ok"}

    def get_info(self):
        return {
            "state": "running",
            "cwd": self._cwd,
        }

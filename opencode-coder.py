#!/usr/bin/env python3
import os
import shutil
import subprocess

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("opencode-coder")


def resolve_opencode() -> str:
    cmd_path = shutil.which("opencode.cmd")
    if cmd_path:
        exe_path = os.path.join(
            os.path.dirname(cmd_path),
            "node_modules",
            "opencode-ai",
            "bin",
            "opencode.exe",
        )
        if os.path.exists(exe_path):
            return exe_path

    return shutil.which("opencode") or shutil.which("opencode.cmd") or "opencode"


@mcp.tool()
def opencode_coder(prompt: str, working_dir: str = ".", timeout_seconds: int = 120) -> dict:
    """调用 OpenCode 在指定项目目录里编写或修改代码。"""
    cmd = [
        resolve_opencode(),
        "run",
        "--format",
        "json",
        "--dangerously-skip-permissions",
        prompt,
    ]

    try:
        result = subprocess.run(
            cmd,
            cwd=working_dir,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        return {
            "success": result.returncode == 0,
            "output": output,
            "return_code": result.returncode
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    mcp.run()

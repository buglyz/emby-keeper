import os
import platform
import sys
from pathlib import Path
from subprocess import Popen

try:
    from msvcrt import getch
except ImportError:

    def getch():
        return b""


from rich import box
from rich.align import Align
from rich.panel import Panel
from rich.table import Table

from . import __name__ as __product__
from . import var
from .cli import app as cli
from .config import config

NO_INSTANT_FLAGS = {"-I", "--no-instant"}


def _notepad_path():
    system_root = os.environ.get("SystemRoot") or r"C:\Windows"
    return str(Path(system_root) / "System32" / "notepad.exe")


def generate_config():
    config.basedir.mkdir(parents=True, exist_ok=True)
    config_file = config.basedir / "config.toml"
    if config_file.exists():
        return
    try:
        with open(config_file, "w+", encoding="utf-8") as f:
            f.write(config.generate_example_config())
    except OSError as e:
        var.console.print(
            f'无法写入默认配置文件 "{config_file}", 请确认是否有权限进行该目录写入: {e}, 请按任意键退出',
            justify="center",
        )
        _ = getch()
        sys.exit(1)
    message = Table.grid(padding=2)
    message.add_column()
    urls = Table.grid(padding=2)
    urls.add_column(style="green", justify="right")
    urls.add_column(no_wrap=True)
    urls.add_row("配置教程", "https://emby-keeper.github.io/guide/配置文件")
    message.add_row("您需要在即将打开的文件中进行账户配置, 您可以根据注释提示进行配置.")
    message.add_row("您也可以参考 Wiki, 链接如下:")
    message.add_row(urls)
    message.add_row(Align("请按任意键以打开模板配置文件", align="center", style="bold blink underline"))
    var.console.print(
        Panel.fit(
            message,
            box=box.ROUNDED,
            padding=(1, 2),
            title="[b red]欢迎您使用 Embykeeper!",
            border_style="bright_blue",
        ),
        justify="center",
    )

    _ = getch()

    p = Popen([_notepad_path(), str(config_file)])

    if not p:
        var.console.print("配置文件打开失败, 请按任意键退出", justify="center")
        _ = getch()
        sys.exit(1)

    var.console.print("等待您编辑配置文件, 请保存关闭编辑器窗口以继续 ...", justify="center")
    p.wait()
    var.console.print(
        f"请确认您配置完成, 并按任意键以继续启动 {__product__.capitalize()}...", justify="center"
    )
    var.console.print(f"配置完成, 即将启动 {__product__.capitalize()} ...", justify="center")


def main():
    generate_config()
    var.console.clear()
    var.console.rule("Embykeeper")
    args = sys.argv[1:]
    if any(arg in NO_INSTANT_FLAGS for arg in args):
        args = args + ["-W"]
    else:
        args = args + ["-W", "-i"]
    cli(args)


if __name__ == "__main__":
    if not platform.system() == "Windows":
        raise RuntimeError("this entrypoint is only for windows")
    main()

"""click/rich keywords that do not exist in click 8.1.8 / rich 13.9.4."""

import click
import rich
from rich.console import Console

# ghostcall-expect: GC004 8
click.echo("hello", newline=True)
# ghostcall-expect: GC004 10
click.echo("hello", nl=True, newlines=2)
# ghostcall-expect: GC004 12
rich.print("done", flush=True, end_="")
# ghostcall-expect: GC004 14 medium
Console().print("table", soft_wrap2=True)
# ghostcall-expect: GC004 16
click.echo("hello", colour=True)

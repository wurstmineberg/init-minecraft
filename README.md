This is **a System V init script for [Minecraft][] servers**, with some [Wurstmineberg][]-specific extras.

This is version 2.19.6 ([semver][Semver]) of the init script. The versioned API includes the usage pattern, as found in the docstring of [`minecraft.py`](minecraft.py), as well as all documented functions defined in minecraft.py.

Requirements
============

*   [a System V-style init system][SysVInit]
*   [Python][] 3.2
*   The current version of the Minecraft server, available from [here][MinecraftDownload] or using the `service minecraft update` command.
*   [docopt][Docopt]
*   [lazyjson][LazyJSON] 1.0 (for whitelist management)
*   [loops][PythonLoops] 1.1 (for server start)
*   [more-itertools][MoreItertools] 2.1
*   [requests][Requests] 2.1 (for updating)

Configuration
=============

If your system has `service`, you can move [`minecraft.py`](minecraft.py) to `/etc/init.d/minecraft`. You can then start, stop, or restart the Minecraft server with `service minecraft start` etc.

To make this work for another server, you may have to modify the paths and other things in the config file.

[Docopt]: https://github.com/docopt/docopt (github: docopt: docopt)
[LazyJSON]: https://github.com/fenhl/lazyjson (github: fenhl: lazyjson)
[Minecraft]: http://minecraft.net/ (Minecraft)
[MinecraftDownload]: https://minecraft.net/download (Minecraft: Download)
[MoreItertools]: http://pypi.python.org/pypi/more-itertools (PyPI: more-itertools)
[Python]: http://python.org/ (Python)
[PythonLoops]: https://gitlab.com/fenhl/python-loops (gitlab: fenhl: python-loops)
[Requests]: http://www.python-requests.org/ (Requests)
[Semver]: http://semver.org/ (Semantic Versioning 2.0.0)
[SysVInit]: https://en.wikipedia.org/wiki/Init#SysV-style (Wikipedia: Init#SysV-style)
[Wurstmineberg]: http://wurstmineberg.de/ (Wurstmineberg)

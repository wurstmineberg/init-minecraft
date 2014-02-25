This is **an init script for [Minecraft][] servers**, with some [Wurstmineberg][]-specific extras.

This version 2.13.18 ([semver][Semver]) of the init script. The versioned API includes the usage pattern, as found in the docstring of [`minecraft.py`](minecraft.py).

Requirements
============

* [Python][] 3.2
* The current version of the Minecraft server, available from [here][MinecraftDownload].
* [docopt][Docopt]

Configuration
=============

If your system has `service`, you can move [`minecraft.py`](minecraft.py) to `/etc/init.d/minecraft`. You can then start, stop, or restart the Minecraft server with `service minecraft start` etc.

To make this work for another server, you may have to modify the paths near the beginning of the file and other things.

[Docopt]: http://github.com/gocopt/docopt (Github: docopt: docopt)
[Minecraft]: http://minecraft.net/ (Minecraft)
[MinecraftDownload]: https://minecraft.net/download (Minecraft: Download)
[Python]: http://python.org/ (Python)
[Semver]: http://semver.org/ (Semantic Versioning 2.0.0)
[Wurstmineberg]: http://wurstmineberg.de/ (Wurstmineberg)

# Third-party components

Timing Studio's first-party source and documentation are MIT licensed. The source archive contains no third-party Python package, JavaScript dependency, Qt binary, browser, font, simulator, foundry data or Python binary.

The optional Windows bootstrap downloads the official **CPython 3.12.10 x64 embeddable package** from https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip . It checks SHA256 `4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3` before extraction. Python's original `LICENSE.txt` and the verified archive are retained in the private runtime. Python and its bundled components remain under their original licenses; this project's MIT license does not replace them. See https://docs.python.org/3/license.html .

Microsoft Edge or Google Chrome must be installed separately. Their respective license terms apply. The interface requests installed system fonts and does not redistribute font files.

Verilog-A output references simulator-provided `constants.vams` and `disciplines.vams`. These files and proprietary simulators are not distributed here. No laboratory server, PDK, device library, proprietary design, user session, access token or account configuration is included.

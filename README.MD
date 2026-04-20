# bambushot

Fire prints on Bambu Lab printers (LAN mode) without re-uploading every time.

Cache files on the printer's SD once, trigger prints in ~600ms forever after.

## Install

```
pip install git+https://github.com/offerrall/bambushot.git
```

`curl` must be in PATH (preinstalled on Windows 10/11, macOS and most Linux).

## Use

```python
from bambushot import BambuPrinter

printer = BambuPrinter(ip='192.168.1.100', access_code='12345678', serial='01P00A...')

printer.upload_gcode('part.gcode.3mf')   # slow, once
printer.print_file('part.gcode.3mf')     # instant, forever
```

## API

```python
printer.list_files()              # what's on the SD
printer.upload_gcode(local_file)  # upload a .gcode.3mf
printer.delete_file(name)         # remove from SD
printer.print_file(name)          # start printing
```

`.gcode.3mf` files must contain exactly one sliced plate (export with *Export plate sliced file*).

For monitoring (progress, camera, temps) use **Bambu Handy**.

## License

MIT
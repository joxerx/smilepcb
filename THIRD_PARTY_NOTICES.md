# Third-party notices

External CAD applications, router binaries, compilers, QMK trees and donor
keyboard designs are not bundled in this source checkout.

| Work | Use and attribution |
| --- | --- |
| [KiCad](https://www.kicad.org/) | Native CAD, ERC/DRC and standard libraries loaded from the local installation. Upstream application/library licenses apply; standard libraries are not bundled. |
| [QMK](https://github.com/qmk/qmk_firmware) | External firmware runtime. Its source/runtime retain upstream license files and applicable distribution obligations. |
| [Freerouting 2.5.0](https://github.com/freerouting/freerouting/tree/v2.5.0) | External GPL-3.0 router; separately provisioned, checksum-checked JAR. |
| [marbastlib](https://github.com/ebastler/marbastlib) | Bundled MX hot-swap/Mini-E footprints and paired symbols, CERN-OHL-P-2.0. Original license and per-file source hashes are in `smilepcb/data/keyboard/`. |
| [Le Chiffre STM32](https://github.com/sporkus/le_chiffre_keyboard_stm32/tree/57f64495c4b6dca1566b8ddbd464849c4023a56f) | Public controller donor, CERN-OHL-W-2.0. CAD is fetched separately; generated derivatives retain COPYING, README and provenance. [Pinned manifest](docs/fixtures/le-chiffre-manifest.json). |
| [KLE](https://github.com/ijprest/keyboard-layout-editor), [kle-ng](https://github.com/adamws/kle-ng) and [kbplacer](https://github.com/adamws/kicad-kbplacer) | Layout interchange and community references; no upstream editor implementation is bundled. |

[marbastlib source pins](smilepcb/data/keyboard/sources.json) identify individual
files and checksums. Additional public references are collected in [community references](docs/sources.md).

SmilePCB's original code is licensed under MIT; see [LICENSE](LICENSE).
Bundled marbastlib assets retain CERN-OHL-P-2.0. The distribution license
expression covers both components. Separately fetched donor CAD and runtime
software retain their own license terms. Generated CAD is not automatically
relicensed under MIT. See upstream notices before redistributing derivatives.

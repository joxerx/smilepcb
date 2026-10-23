# Rules and public sources

SmilePCB turns known design practices into parameterized operations and checks.
A community reference is evidence for a design choice, not proof that every
modified board will work. Component variants, fabrication limits and assembly
constraints remain explicit inputs.

| Area | Rule used by SmilePCB | References |
| --- | --- | --- |
| Controller | Reuse a pinned STM32F072 circuit source; check GPIO allocation, power and interface access after placement. Keep its provenance and license. | [Le Chiffre STM32](https://github.com/sporkus/le_chiffre_keyboard_stm32/tree/57f64495c4b6dca1566b8ddbd464849c4023a56f), [source manifest](fixtures/le-chiffre-manifest.json), [STM32F072CB documentation](https://www.st.com/en/microcontrollers-microprocessors/stm32f072cb.html#documentation) |
| Mechanical placement | Reserve switch, socket, diode, LED and controller envelopes before routing; use actual courtyard and copper geometry. | [Keyboard Atelier libraries](https://wiki.kbatelier.org/books/footprints-symbols-libraries/page/common-libraries), [ai03 guide](https://wiki.kbatelier.org/books/ai03-pcb-design-guide-wip/page/pcb-guide-part-1-preparations), [marbastlib](https://github.com/ebastler/marbastlib) |
| Per-key RGB | Reverse-mount Mini-E needs a compatible cutout, pad orientation, solder access and switch clearance. Treat non-E variants as different parts. | [Corne v3 build guide](https://github.com/foostan/crkbd/blob/main/docs/corne-cherry/v3/buildguide_en.md), [Sofle Choc guide](https://github.com/josefadamcik/SofleKeyboard/blob/master/docs/build_guide_choc.md), [Kyria errata](https://docs.splitkb.com/product-guides/kyria/changelog) |
| Matrix and firmware | Derive matrix cells, physical keys, nets and firmware pins from one model; reserve protocol and RGB pins before matrix allocation. | [QMK matrix explanation](https://docs.qmk.fm/how_a_matrix_works), [QMK configuration](https://docs.qmk.fm/reference_info_json) |
| RGB chain and power | Emit QMK's LED map from the actual serial chain. Check logic levels and the selected LED current budget; control power-up/suspend behavior. Brightness limiting is not hardware current protection. | [QMK RGB Matrix](https://docs.qmk.fm/features/rgb_matrix), [QMK WS2812 driver](https://docs.qmk.fm/drivers/ws2812) |
| USB connection | Model the daughterboard connector and controller USB pair explicitly; retain native pin/net, width and length checks. Connector/cable geometry needs a separate mechanical review. | [Unified Daughterboard](https://unified-daughterboard.github.io/), [Le Chiffre STM32](https://github.com/sporkus/le_chiffre_keyboard_stm32/tree/57f64495c4b6dca1566b8ddbd464849c4023a56f) |
| Routing | Prepare controller interfaces, then rows, columns and the RGB snake. Prefer grouped orthogonal/45° paths; validate layer changes and via lands. Global recovery starts from a checked generated seed. | [KiCad PCB documentation](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html), [Freerouting](https://github.com/freerouting/freerouting) |
| Acceptance | Native ERC/DRC and schematic parity, independent pad connectivity, source-preservation checks and a matching clean QMK build are separate gates. No gate establishes fabricated hardware performance. | [KiCad CLI](https://docs.kicad.org/10.0/en/cli/cli.html), [QMK build environment](https://docs.qmk.fm/newbs_getting_started) |

Useful complementary tools:

- [Keyboard Layout Editor](https://github.com/ijprest/keyboard-layout-editor): layout interchange.
- [kle-ng](https://github.com/adamws/kle-ng): layout editing, plate and PCB generation.
- [kbplacer](https://github.com/adamws/kicad-kbplacer): KiCad keyboard placement/routing automation.
- [Keyboard Atelier Wiki](https://wiki.kbatelier.org/): community design and library resources.

These links acknowledge sources and useful tools; they do not imply affiliation
or endorsement. Bundled files have hashes and license attribution in
[marbastlib provenance](../smilepcb/data/keyboard/sources.json). Separately fetched
sources keep their upstream licenses; see [third-party notices](../THIRD_PARTY_NOTICES.md).

For a new recipe, contribute the exact public revision/datasheet, applicable
constraints, failure cases and an executable validation example. See the
[contributor guide](../CONTRIBUTING.md).

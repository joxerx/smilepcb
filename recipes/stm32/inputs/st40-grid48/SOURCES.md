# ST40 layout-derived 48-key fixture

This fixture uses the ordinary STM32 generator with a 4 by 12 grid of individual
1u keys. It tests fresh KLE generation on a smaller public-reference layout.
It does not route the original ST40 PCB or qualify that hardware.

The public geometry reference is [coarse/st40 at
85a298d5c642bd3f0558771611119981649b09ad](https://github.com/coarse/st40/tree/85a298d5c642bd3f0558771611119981649b09ad).
The original `st40.kicad_pcb` SHA-256 is
`cf1427e4dc9ba7c6fcd6038defa56626babeb27c3a4b239e22903cb20586ea04`.
The design is attributed to Michael Pio (Miyo), under
[CC BY-SA 4.0](https://github.com/coarse/st40/blob/85a298d5c642bd3f0558771611119981649b09ad/LICENSE).
This layout-derived fixture is offered under the same CC BY-SA 4.0 terms.

The source has 48 individual 1u switch positions plus an overlapping optional
2u space footprint; this fixture selects only the 48 individual positions.
Key legends and QMK keycodes are explicit fixture choices. The original has
solder MX switches, a bare STM32F072 and no RGB. This generator retains its
own hotswap switch, controller, diode, board-outline and daughterboard recipes;
RGB is disabled. No original copper, circuit, footprints or PCB files are copied
into the generated board. Its output is a new board requiring its own native
ERC/DRC, connectivity and matching QMK checks.

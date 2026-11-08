# Reference-style routing

The0.3.0 profile is selected with `--unified-controller-plan`,
`--component-corridors`, `--coherent-via-banks` and `--routing-quality turns`.
The README records the complete tested build command and budgets.
These options are explicit rather than silently changing the older defaults.

## Geometry and ordering

Rows use long horizontal trunks; the column field uses vertical and45-degree
segments on the other copper face. The profile follows the actual controller
face and available geometry, rather than requiring all keyboards to use one
fixed front/back assignment. Controller feeders may cross the field through
checked layer transitions and shared corridors.

The controller module is prepared once, placed against actual native pad/hole,
courtyard and board-edge geometry, and checked for local power/reset/boot
closure. USB is prepared before matrix completion. Matrix functions receive
physical source heads, a consistent GPIO/model mapping and grouped approaches.
Later completion must preserve the qualified matrix field and required source
geometry while joining RGB, power and returns.

Parallel lanes may turn together; minimum bend count alone does not define
quality. The implementation uses geometric cost, physical corridor capacity,
preferred matrix faces and native collision/contact checks. It contains no
per-layout manual route coordinates for the qualified inputs.

## USB and layer transitions

General matrix/RGB/power routing may use through vias on the two copper layers.
The current USB recipe is intentionally narrower: both conductors remain on
the controller face without vias. Search, balancing and verification use that
same recipe. Setting `--usb-max-length-mm none` removes only the absolute
geometric length cap; it does not remove pair-skew or native clearance checks.
No USB impedance, return-current or hardware qualification is inferred from
these software tests.

## Qualification and limits

Release evidence reports complete raw-KLE builds, including rejected automatic
placements, rather than manually chosen successful checkpoints. Check final
ERC/DRC/parity, saved-board connectivity, preserved key/LED centres and matching
clean firmware receipts. A locally accepted placement is not a completed PCB.

The0.3.0 repeat cohort is being finalized before publication. Its scope is
ANSI61 and Compact68 with RGB, two copper layers and the pinned toolchain.
Arbitrary layouts, dense40% fixed-outline controller placement and ANSI104 RGB
are outside that qualification scope. Exact geometry and timing can vary with
search deadlines and native IDs; repeated success is not universal proof.

The routing ideas are informed by the public designs and tools linked in
[Sources](sources.md). Reference appearance is an engineering goal, not a claim
that every generated local jog, via or MCU approach matches a hand-routed board.

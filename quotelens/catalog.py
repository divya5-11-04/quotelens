"""Materials catalog. Base prices are rough illustrative INR values used only to generate demo data;
they are not market data. Replace them with your own price history."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Material:
    id: str
    name: str
    unit: str
    base_price: float
    aliases: tuple = ()
    mm: str = ""  # diameter / thickness in mm, used to stop 12mm matching 16mm


def _tmt(d, price):
    return Material(f"tmt_{d}", f"TMT Bar Fe500D {d}mm", "kg", price,
                    (f"tmt rebar fe 500d {d} mm", f"steel reinforcement bar {d}mm"), str(d))


CATALOG = [
    Material("cement_opc53", "OPC 53 Grade Cement", "bag", 390, ("ordinary portland cement 53", "opc 53 cement 50 kg bag")),
    Material("cement_ppc", "PPC Cement", "bag", 365, ("portland pozzolana cement",)),
    _tmt(8, 62), _tmt(10, 61), _tmt(12, 60), _tmt(16, 60), _tmt(20, 60),
    Material("river_sand", "River Sand", "tonne", 1800, ("natural sand", "washed river sand")),
    Material("msand", "M-Sand", "tonne", 1150, ("manufactured sand", "crushed sand")),
    Material("agg_20", "20mm Aggregate", "tonne", 1050, ("20 mm jelly", "coarse aggregate 20mm", "blue metal 20mm"), "20"),
    Material("brick_red", "Red Clay Brick", "nos", 9, ("wirecut brick", "clay brick first class")),
    Material("brick_flyash", "Fly Ash Brick", "nos", 7, ("fly ash bricks 230x110x75",)),
    Material("aac_block", "AAC Block 600x200x200", "nos", 68, ("autoclaved aerated concrete block",)),
    Material("rmc_m25", "RMC M25 Concrete", "cum", 5600, ("ready mix concrete m25",)),
    Material("binding_wire", "Binding Wire 18 Gauge", "kg", 78, ("tying wire", "ms binding wire")),
    Material("plywood_18", "Plywood 18mm MR Grade", "sheet", 2900, ("mr plywood 18 mm", "marine ply 18mm"), "18"),
]
BY_ID = {m.id: m for m in CATALOG}

# canonical units and conversions into the catalog unit: rate_in_catalog_unit = rate * factor
UNIT_ALIASES = {"bag": "bag", "bags": "bag", "kg": "kg", "kgs": "kg", "tonne": "tonne", "tonnes": "tonne",
                "mt": "tonne", "nos": "nos", "no": "nos", "cum": "cum", "cft": "cft", "sqft": "sqft",
                "sqm": "sqm", "rmt": "rmt", "ltr": "ltr", "sheet": "sheet", "sheets": "sheet"}
RATE_FACTOR = {("tonne", "kg"): 1 / 1000, ("kg", "tonne"): 1000.0}

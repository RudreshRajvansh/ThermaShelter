from __future__ import annotations

from dataclasses import dataclass

from .schema import (Facade, OccupancySpec, PlanSpec, ShelterUse, SourceRef,
                     SourceStatus)

SPHERE = SourceRef(
    citation=("Sphere Handbook 2018, Shelter and Settlement Standard 3, minimum "
              "3.5 square metres of covered living area per person, with 4.5 to "
              "5.5 square metres advised in cold climates where people spend the "
              "day indoors"),
    status=SourceStatus.VERIFIED)

ASSUMED_PROGRAMME = SourceRef(
    citation=("Engineering assumption. No published scale of accommodation was "
              "available for this shelter type at the time of writing"),
    status=SourceStatus.ASSUMED,
    note=("Area per person, ceiling height and internal gains all move the answer. "
          "Replace with the establishment's own scale of accommodation before any "
          "number from this run is quoted."))

DOOR_AREA_M2 = 1.80
MIN_ROOM_WIDTH_M = 1.20
MIN_ROOM_AREA_M2 = 2.50


@dataclass(frozen=True)
class Room:
    name: str
    fraction: float
    south: bool
    heated: bool = True


@dataclass(frozen=True)
class Programme:
    use: ShelterUse
    label: str
    description: str
    area_per_person_m2: float
    occupants: int
    ceiling_height_m: float
    aspect_ratio: float
    metabolic_w_per_person: float
    equipment_w_per_person: float
    lighting_w_per_m2: float
    cooking_w: float
    moisture_g_h_per_person: float
    occupied_from_hour: int
    occupied_until_hour: int
    daytime_occupancy_fraction: float
    rooms: tuple[Room, ...]
    source: SourceRef

    @property
    def floor_area_m2(self) -> float:
        return round(self.area_per_person_m2 * max(self.occupants, 1), 1)


PROGRAMMES: dict[ShelterUse, Programme] = {
    ShelterUse.BARRACK: Programme(
        use=ShelterUse.BARRACK,
        label="Troop accommodation",
        description=("A sleeping hut for a section. Occupied through the night and "
                     "largely empty during the working day, so the design problem is "
                     "holding overnight temperature with no heating running."),
        area_per_person_m2=9.0, occupants=10, ceiling_height_m=2.60,
        aspect_ratio=2.20, metabolic_w_per_person=80.0,
        equipment_w_per_person=15.0, lighting_w_per_m2=4.0, cooking_w=0.0,
        moisture_g_h_per_person=50.0,
        occupied_from_hour=18, occupied_until_hour=7,
        daytime_occupancy_fraction=0.15,
        rooms=(Room("Sleeping bay east", 0.34, True),
               Room("Sleeping bay west", 0.34, True),
               Room("Kit store", 0.10, False),
               Room("Ablution", 0.12, False),
               Room("Entry lobby", 0.10, False, heated=False)),
        source=ASSUMED_PROGRAMME),
    ShelterUse.MEDICAL_POST: Programme(
        use=ShelterUse.MEDICAL_POST,
        label="Medical post",
        description=("A treatment room and a small ward. Held at a steadier "
                     "temperature than the other types and occupied around the "
                     "clock, so the overnight minimum matters more than the mean."),
        area_per_person_m2=12.0, occupants=6, ceiling_height_m=2.80,
        aspect_ratio=1.60, metabolic_w_per_person=100.0,
        equipment_w_per_person=60.0, lighting_w_per_m2=8.0, cooking_w=0.0,
        moisture_g_h_per_person=60.0,
        occupied_from_hour=0, occupied_until_hour=0,
        daytime_occupancy_fraction=0.80,
        rooms=(Room("Treatment room", 0.30, True),
               Room("Ward", 0.34, True),
               Room("Pharmacy and store", 0.14, False),
               Room("Staff room", 0.12, False),
               Room("Entry lobby", 0.10, False, heated=False)),
        source=ASSUMED_PROGRAMME),
    ShelterUse.COMMAND_POST: Programme(
        use=ShelterUse.COMMAND_POST,
        label="Command and communications post",
        description=("Manned continuously with radio and computing equipment "
                     "running. Equipment heat is a real part of the balance here "
                     "and is not a rounding error."),
        area_per_person_m2=6.0, occupants=8, ceiling_height_m=2.70,
        aspect_ratio=1.40, metabolic_w_per_person=100.0,
        equipment_w_per_person=180.0, lighting_w_per_m2=9.0, cooking_w=0.0,
        moisture_g_h_per_person=45.0,
        occupied_from_hour=0, occupied_until_hour=0,
        daytime_occupancy_fraction=1.0,
        rooms=(Room("Operations room", 0.42, True),
               Room("Radio room", 0.20, False),
               Room("Rest bay", 0.18, True),
               Room("Equipment store", 0.10, False),
               Room("Entry lobby", 0.10, False, heated=False)),
        source=ASSUMED_PROGRAMME),
    ShelterUse.KITCHEN_MESS: Programme(
        use=ShelterUse.KITCHEN_MESS,
        label="Cookhouse and mess",
        description=("Large intermittent cooking gains and heavy moisture release. "
                     "The design question is as much about getting moisture out as "
                     "about keeping heat in, so condensation hours carry weight."),
        area_per_person_m2=2.20, occupants=24, ceiling_height_m=3.00,
        aspect_ratio=1.80, metabolic_w_per_person=120.0,
        equipment_w_per_person=25.0, lighting_w_per_m2=10.0, cooking_w=4500.0,
        moisture_g_h_per_person=180.0,
        occupied_from_hour=6, occupied_until_hour=21,
        daytime_occupancy_fraction=0.55,
        rooms=(Room("Dining hall", 0.48, True),
               Room("Kitchen", 0.24, False),
               Room("Dry store", 0.10, False),
               Room("Wash up", 0.10, False),
               Room("Entry lobby", 0.08, False, heated=False)),
        source=ASSUMED_PROGRAMME),
    ShelterUse.WORKSHOP: Programme(
        use=ShelterUse.WORKSHOP,
        label="Workshop",
        description=("A tall bay with a work floor and a small office. Occupied on "
                     "the day shift only, with large doors that make infiltration "
                     "the dominant loss."),
        area_per_person_m2=16.0, occupants=4, ceiling_height_m=3.40,
        aspect_ratio=1.50, metabolic_w_per_person=160.0,
        equipment_w_per_person=350.0, lighting_w_per_m2=11.0, cooking_w=0.0,
        moisture_g_h_per_person=90.0,
        occupied_from_hour=8, occupied_until_hour=18,
        daytime_occupancy_fraction=1.0,
        rooms=(Room("Work bay", 0.62, True),
               Room("Tool store", 0.16, False),
               Room("Office", 0.14, True),
               Room("Entry lobby", 0.08, False, heated=False)),
        source=ASSUMED_PROGRAMME),
    ShelterUse.STORE: Programme(
        use=ShelterUse.STORE,
        label="Unheated store",
        description=("Holds stores rather than people. Nobody is comfortable or "
                     "uncomfortable in it, so comfort hours mean nothing here and "
                     "the useful question is how many hours it spends below "
                     "freezing."),
        area_per_person_m2=30.0, occupants=1, ceiling_height_m=3.00,
        aspect_ratio=2.00, metabolic_w_per_person=110.0,
        equipment_w_per_person=0.0, lighting_w_per_m2=3.0, cooking_w=0.0,
        moisture_g_h_per_person=0.0,
        occupied_from_hour=9, occupied_until_hour=11,
        daytime_occupancy_fraction=0.05,
        rooms=(Room("Store floor", 0.86, True),
               Room("Entry lobby", 0.14, False, heated=False)),
        source=ASSUMED_PROGRAMME),
    ShelterUse.RELIEF_SHELTER: Programme(
        use=ShelterUse.RELIEF_SHELTER,
        label="Family relief shelter",
        description=("A displaced family in a cold climate, indoors most of the "
                     "day. Sized on the Sphere covered area standard rather than on "
                     "a military scale of accommodation."),
        area_per_person_m2=5.0, occupants=5, ceiling_height_m=2.50,
        aspect_ratio=1.30, metabolic_w_per_person=90.0,
        equipment_w_per_person=20.0, lighting_w_per_m2=4.0, cooking_w=1200.0,
        moisture_g_h_per_person=110.0,
        occupied_from_hour=16, occupied_until_hour=9,
        daytime_occupancy_fraction=0.50,
        rooms=(Room("Living and sleeping", 0.52, True),
               Room("Second sleeping room", 0.24, True),
               Room("Cooking corner", 0.14, False),
               Room("Entry lobby", 0.10, False, heated=False)),
        source=SPHERE),
}


def programme(use: ShelterUse) -> Programme:
    return PROGRAMMES[use]


def plan_for(use: ShelterUse, azimuth_deg: float = 180.0,
             floor_area_m2: float | None = None) -> PlanSpec:
    spec = programme(use)
    return PlanSpec(use=use,
                    floor_area_m2=floor_area_m2 or spec.floor_area_m2,
                    aspect_ratio=spec.aspect_ratio,
                    ceiling_height_m=spec.ceiling_height_m,
                    azimuth_deg=azimuth_deg)


def occupancy_for(use: ShelterUse, floor_area_m2: float | None = None,
                  occupants: int | None = None) -> OccupancySpec:
    spec = programme(use)
    people = spec.occupants if occupants is None else occupants
    area = floor_area_m2 or spec.floor_area_m2
    return OccupancySpec(
        occupants=people,
        metabolic_w_per_person=spec.metabolic_w_per_person,
        equipment_w=round(spec.equipment_w_per_person * people, 1),
        lighting_w=round(spec.lighting_w_per_m2 * area, 1),
        cooking_w=spec.cooking_w,
        occupied_from_hour=spec.occupied_from_hour,
        occupied_until_hour=spec.occupied_until_hour,
        daytime_occupancy_fraction=spec.daytime_occupancy_fraction,
        additional_moisture_g_h=round(spec.moisture_g_h_per_person * people, 1),
        unoccupied=people == 0,
        source=spec.source)


@dataclass(frozen=True)
class RoomFootprint:
    name: str
    x0: float
    x1: float
    y0: float
    y1: float
    heated: bool

    @property
    def area_m2(self) -> float:
        return (self.x1 - self.x0) * (self.y1 - self.y0)

    @property
    def centre(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2.0, (self.y0 + self.y1) / 2.0)


@dataclass(frozen=True)
class PartitionRun:
    x0: float
    y0: float
    x1: float
    y1: float
    between: tuple[str, str]

    @property
    def length_m(self) -> float:
        return ((self.x1 - self.x0) ** 2 + (self.y1 - self.y0) ** 2) ** 0.5


@dataclass(frozen=True)
class Layout:
    rooms: tuple[RoomFootprint, ...]
    partitions: tuple[PartitionRun, ...]
    height_m: float
    notes: tuple[str, ...]

    @property
    def partition_area_m2(self) -> float:
        gross = sum(run.length_m for run in self.partitions) * self.height_m
        doors = DOOR_AREA_M2 * len(self.partitions)
        return max(gross - doors, 0.0)


def _band(rooms: list[Room], x0: float, x1: float, y0: float, y1: float,
          out: list[RoomFootprint], cuts: list[tuple[float, str, str]]) -> None:
    total = sum(room.fraction for room in rooms)
    if total <= 0.0:
        return
    cursor = x0
    for index, room in enumerate(rooms):
        width = (x1 - x0) * room.fraction / total
        right = x1 if index == len(rooms) - 1 else cursor + width
        out.append(RoomFootprint(room.name, cursor, right, y0, y1, room.heated))
        if index < len(rooms) - 1:
            cuts.append((right, room.name, rooms[index + 1].name))
        cursor = right


def layout_for(plan: PlanSpec) -> Layout:
    spec = programme(plan.use)
    width, depth = plan.width_m, plan.depth_m
    notes: list[str] = []

    rooms = [room for room in spec.rooms if room.fraction > 0.0]
    if not rooms:
        rooms = [Room("Interior", 1.0, True)]

    south = [room for room in rooms if room.south]
    north = [room for room in rooms if not room.south]
    if not south:
        south, north = north, []

    south_share = sum(room.fraction for room in south)
    total = sum(room.fraction for room in rooms)
    split_y = depth * (south_share / total) if north else depth

    footprints: list[RoomFootprint] = []
    cuts_south: list[tuple[float, str, str]] = []
    cuts_north: list[tuple[float, str, str]] = []
    _band(south, 0.0, width, 0.0, split_y, footprints, cuts_south)
    if north:
        _band(north, 0.0, width, split_y, depth, footprints, cuts_north)

    partitions: list[PartitionRun] = []
    for x, left, right in cuts_south:
        partitions.append(PartitionRun(x, 0.0, x, split_y, (left, right)))
    for x, left, right in cuts_north:
        partitions.append(PartitionRun(x, split_y, x, depth, (left, right)))
    if north:
        partitions.append(PartitionRun(
            0.0, split_y, width, split_y,
            (south[0].name, north[0].name)))

    narrow = [room for room in footprints
              if min(room.x1 - room.x0, room.y1 - room.y0) < MIN_ROOM_WIDTH_M
              or room.area_m2 < MIN_ROOM_AREA_M2]
    if narrow:
        notes.append(
            f"{len(narrow)} of the {len(footprints)} rooms in this layout come out "
            f"below {MIN_ROOM_WIDTH_M:.1f} m across or {MIN_ROOM_AREA_M2:.1f} square "
            "metres at this floor area. The layout is drawn as it falls out of the "
            "programme rather than being quietly adjusted, because the honest "
            "reading is that the shelter is too small for this use.")
    notes.append(
        "The rooms and partitions are the layout the programme implies at this "
        "floor area and orientation. They are not separate thermal zones: the "
        "simulation solves one air volume, and the partition walls enter it as "
        "internal mass, not as boundaries between different temperatures.")
    return Layout(tuple(footprints), tuple(partitions), plan.ceiling_height_m,
                  tuple(notes))

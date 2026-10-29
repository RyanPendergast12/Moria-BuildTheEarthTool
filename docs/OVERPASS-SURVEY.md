# Automatic overpasses: Summerlin survey (v0.4.3, approach grade 0.06, minimum clearance 4)

Produced by `prep/ov_survey_auto.py` with the same discovery and eligibility code a snowball uses (`prep/crossings.py`). Candidates: every OSM carriageway bridge over an at-grade road in the Summerlin box (`-115.36,36.12,-115.26,36.22`), grouped into structures.

| crossing | key | result | clearance (min air blocks / measured top-to-top) | whole-crossing plan, or skip reason |
|---|---|---|---|---|
| Alta Drive over Bruce Woodbury Beltway (motorway) | `x_w663722432_w82526247` | **eligible** | 5 blocks / 7.72 m, 6 blocks / 8.65 m | 32683 blocks, 166 chunks |
| Bruce Woodbury Beltway over Summerlin Centre Drive (secondary) | `x_w98396174_w98396175` | **eligible** | 4 blocks / 7.35 m | 28172 blocks, 109 chunks |
| Crestdale Lane over Summerlin Parkway (motorway) | `x_w27450152` | **eligible** | 4 blocks / 7.33 m, 6 blocks / 7.58 m | 13462 blocks, 102 chunks |
| North Anasazi Drive over Summerlin Parkway (motorway) | `x_w27450156_w389096002` | **eligible** | 5 blocks / 8.09 m, 5 blocks / 7.81 m | 39439 blocks, 250 chunks |
| North Durango Drive over ? (motorway_link), Summerlin Parkway (motorway) | `x_w152184592_w651364727` | **eligible** | 5 blocks / 8.07 m, 6 blocks / 8.48 m | 21152 blocks, 113 chunks |
| West Charleston Boulevard over Bruce Woodbury Beltway (motorway) | `x_w234014889_w234014891` | **eligible** | 5 blocks / 7.1 m, 5 blocks / 7.53 m | 45847 blocks, 192 chunks |
| West Cheyenne Avenue over Bruce Woodbury Beltway (motorway) | `x_w27631326_w488751541` | **eligible** | 5 blocks / 8.62 m, 6 blocks / 9.3 m | 61004 blocks, 353 chunks |
| West Desert Inn Road over Bruce Woodbury Beltway (motorway) | `x_w511596525_w552707978` | **eligible** | 4 blocks / 7.21 m, 5 blocks / 7.26 m | 20764 blocks, 110 chunks |
| West Sahara Avenue over Bruce Woodbury Beltway (motorway) | `x_w269010370_w97640965` | **eligible** | 5 blocks / 7.29 m, 5 blocks / 7.84 m | 42584 blocks, 166 chunks |
| (ramp structure) over Bruce Woodbury Beltway (motorway) | `x_w1526526037_n24_c3d69b6fa0` | skipped | - | data: lower road elevation uncertain: w27524270: lower road surface not measured on both sides of the deck; w299859889: lower road surface not measured on both sides of the deck |
| (ramp structure) over Bruce Woodbury Beltway (motorway) | `x_w1070309494_n24_df821ed223` | skipped | - | data: lower road elevation uncertain: w493247577: lower road surface not measured on both sides of the deck; w665225700: lower road surface not measured on both sides of the deck |
| (ramp structure) over Bruce Woodbury Beltway (motorway) | `x_w145182541_n16_471f5191b7` | skipped | - | data: lower road elevation uncertain: w1477010347: lower road surface not measured on both sides of the deck; w1477010349: lower road surface not measured on both sides of the deck |
| Summerlin Parkway over North Rampart Boulevard (primary) | `x_w27613901` | skipped | - | data: lower road elevation uncertain: w1025141268: lower road surface not measured on both sides of the deck; w388329213: lower road surface not measured on both sides of the deck |
| Summerlin Parkway over North Rampart Boulevard (primary) | `x_w27613904` | skipped | - | data: lower road elevation uncertain: w1025141268: lower road surface not measured on both sides of the deck; w388329213: lower road surface not measured on both sides of the deck |
| Summerlin Parkway over North Town Center Drive (primary) | `x_w27613907_w27613910` | skipped | - | geometry: the measured deck is 2 separate structures; one connected deck per crossing is supported |
| Summerlin Parkway over ? (motorway_link), Bruce Woodbury Beltway (motorway) | `x_w1391305290_n6_99bccd2539` | skipped | 5 blocks / 7.58 m, 5 blocks / 7.46 m, 5 blocks / 7.94 m | data: measured deck length 23 m vs OSM bridge 15 m (w1391305290): inconsistent; data: measured deck length 11 m vs OSM bridge 7 m (w1534115945): inconsistent |

**Reading the reasons**
- `data:` the evidence is insufficient (LiDAR, lower road both sides, deck/OSM consistency).
- `geometry:` the structure isn't a simple overpass (separate decks, separation outside 4–15 m, too wide).
- `clearance:`, `approach:`, `overlap:` and `strip:` are as in the README eligibility table.

Protected-build conflicts are decided in-game by the whole-crossing preflight, so they don't appear here.

# Running thermostat schedules for human comfort

Working notes the planner is built on. Each rule says where it comes from and
how firm it is. Figures attributed to ASHRAE 55 and ISO 7730 come from
secondary summaries and papers that quote them, not from the standards' own
text.

## 1. What comfort depends on

Six variables set thermal sensation (Fanger's PMV model, ISO 7730, ASHRAE 55):

| Variable | In this house | How the planner gets it |
|---|---|---|
| Air temperature | What the Nest measures and controls | Thermostat status |
| Mean radiant temperature | Warm floor raises it; cold windows and walls lower it | Modelled from outdoor forecast, insulation and window type |
| Air speed | Low: radiant floors, no fan | Fixed at 0.1 m/s |
| Humidity | 53-56% measured | Thermostat status |
| Clothing | Indoor winter clothing, about 1.0 clo with furniture | Intake form, small seasonal adjustment |
| Activity | 0.7 met asleep to about 1.3 met playing or cooking | Zone use from the intake form |

- PMV between -0.5 and +0.5 is the accepted comfort band; about 10% of people
  are still dissatisfied inside it (standard). No setpoint satisfies everyone.
- Operative temperature (roughly the mean of air and mean radiant temperature)
  is what people feel. A thermostat holds air temperature, so on cold days the
  same setpoint feels cooler. The planner raises the air setpoint slightly as
  the forecast gets colder to hold operative temperature constant.
- At 1.0 clo the winter comfort zone is roughly 20-24 C operative (secondary
  sources). Humidity inside the normal range has a small effect.
- Individual differences are larger than the model's precision. Research
  disagrees on age and sex effects: Fanger's lab work found no age difference
  in preferred temperature, while field studies in care homes found people aged
  80+ preferring 1.5-2 C warmer; women in one study preferred warmer conditions,
  partly through lighter clothing. Rule used: start from each person's stated
  preference and use the model only for how that preference moves with
  conditions.

## 2. Sleep

- Adults: about 60-67 F, 65 F a common target (widely repeated guidance).
- Children: about 65-70 F. Infants: 68-72 F is common paediatric guidance,
  with overheating a known risk; the evidence base is thin.
- Adults 65+: one small 2023 study (about 50 people) found best sleep between
  68 and 77 F with large differences between individuals, and sleep efficiency
  falling above 77 F.
- Rule used: explicit sleep preference if given, otherwise a default by age
  group adjusted for bedding and for whether the person runs warm or cold.
  Bedroom zones are not warmed to full daytime temperature for the short time
  people are getting up or going to bed.

## 3. Radiant floors change the scheduling rules

All four zones are gas-fired in-floor radiant, heat only.

- Slabs respond over hours. A Montana State University test measured 9 to 19
  hours for an insulated slab to pick up after a supply-temperature change.
  Homeowners report 8-12 hours to recover from deep setbacks (anecdotal).
- Deep night or daytime setbacks backfire: the room is cold in the morning and
  the boiler runs hard to recover. The common recommendation is a setback of
  2-3 F at most, and none for short absences. Long trips are the exception.
- Recovery must start well before people need the heat, and heat can be cut
  before they leave or go to bed because the slab keeps emitting.
- Aggressive boiler outdoor reset slows recovery further.
- Floor surface temperature: ISO 7730:2005 gives 19-29 C for people in normal
  shoes (under 10% dissatisfied), 17-31 C for its loosest class; ASHRAE 55 uses
  the same 19-29 C range. This caps how hard a floor can be driven, so a
  setpoint the system cannot reach in time only produces long boiler runs. The
  planner caps how far ahead it will start a warm-up. (A 2025 edition of ISO
  7730 exists; its figures were not checked.)
- Rules used: setback 2 F by default and only for gaps of 3 hours or more;
  warm-ups start early by (temperature rise / zone warm-up rate), slower when
  colder outside, capped at 3 hours (2 in bedroom zones); cool-downs start 1
  hour early. Warm-up rates come from the intake form until history refines
  them.

## 4. Using the forecast

- Predictive control of floor heating is studied mainly because it offsets the
  slow response: restart times are optimised from occupancy and weather.
  Reported savings range from about 4% to 12%, mostly from simulations.
- Colder forecast: longer warm-up lead, slightly higher air setpoint.
- Sunny forecast: sun-exposed rooms overshoot when the slab is already warm, so
  the planner trims the midday target by up to 1 F in zones marked sunny.
- Seattle context: heating season is mild and cloudy; cooling is not available
  on this system, so summer comfort is outside what the schedule can control.

## 5. Safety and practical limits

- The planner never schedules below 55 F or above 76 F regardless of profile,
  and stays inside the household's own minimum and maximum.
- WHO Housing and Health Guidelines (2018): indoor temperatures should be high
  enough to protect against cold; 18 C (64 F) is given as a starting point, not
  a hard threshold, drawn from earlier findings of no demonstrable risk to
  healthy sedentary people between 18 and 24 C. A higher minimum may be needed
  for older people, children and people with chronic illness.
- Infants and older adults are never left more than 1 F below their own target
  when sharing a zone.
- Nest schedules: push the complete 7-day schedule, at least 15 seconds apart;
  learning mode must be off or the device rewrites the schedule. Day "0" is
  Monday in the device format.

## 6. What this cannot do

- Different people sharing one zone get a compromise. The report states the
  largest gap for each person.
- One reading per day is too sparse to learn slab dynamics well. The history
  trim is deliberately small (within 1 F).
- Comfort also depends on drafts, floor coverings and where the thermostat is
  mounted. Notes about those belong in the intake form.

## Sources

- Montana State University, radiant floor test: https://www.montana.edu/idl/pdf/RMEH%2004.pdf
- InspectApedia, radiant slab setback: https://inspectapedia.com/heat/Radiant-slab-heat-setback-temperatures.php
- Green Building Advisor, radiant setback: https://greenbuildingadvisor.com/article/should-i-turn-down-radiant-floor-heat-at-night
- ASHRAE 55 summary: https://www.simscale.com/blog/ashrae-standard-55-thermal-comfort/
- ASHRAE 55-2017 addendum d: https://www.ashrae.org/file%20library/technical%20resources/standards%20and%20guidelines/standards%20addenda/55_2017_d_20200731.pdf
- Sleep temperature overview: https://healthline.com/health/sleep/best-temperature-to-sleep
- Older adults and sleep temperature (coverage of 2023 study): https://www.deseret.com/2023/8/30/23852422/best-temperature-for-sleep-study-science/
- Thermal comfort diversity review: https://pmc.ncbi.nlm.nih.gov/articles/PMC6298492
- Forecast-based radiant floor control (Cesari et al. 2022): https://iris.unife.it/bitstream/11392/2475553/1/2022_A%20weather%20forecast%20based%20control.pdf
- Floor heating MPC (Zanetti et al. 2022): https://re.public.polimi.it/retrieve/bf1c8622-4bd9-4c11-b61f-5aec4979e94c/Floor%20heating%20pre-onoff%20parameters%20based%20on%20Model%20Predictive%20Control%20feature%20extrapolation.pdf
- ISO 7730 floor temperature limits (REHVA): https://rehva.eu/rehva-journal/chapter/iso-11855-the-international-standard-on-the-design-dimensioning-installation-and-control-of-embedded-radiant-heating-and-cooling-systems
- Comfortable floor temperatures (Wu et al.): https://acris.aalto.fi/ws/portalfiles/portal/66233514/ENG_Wu_et_al_Comfortable_floor_temperatures_for_radiant_Chinese_Science_Bulletin.pdf
- WHO Housing and Health Guidelines, low indoor temperatures: https://www.ncbi.nlm.nih.gov/books/NBK535294/
- NLE schedule API: https://docs.nolongerevil.com/api-reference/v1/schedule

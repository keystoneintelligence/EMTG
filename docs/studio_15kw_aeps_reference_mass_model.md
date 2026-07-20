# Studio 15 kW AEPS reference mass model

The default EMTG Studio Earth-to-asteroid search needs a spacecraft mass model
that is useful for architecture search without claiming to represent a finished
flight design. The selected defaults are:

| Quantity | Default | EMTG option |
| --- | ---: | --- |
| Maximum departure mass | 2,500 kg | `maximum_mass` |
| Inclusive dry-mass floor | 1,200 kg | `final_mass_constraint_bounds` with `constrain_dry_mass` |
| Installed xenon capacity | 1,000 kg | `maximum_electric_propellant` |
| Xenon reserve | 10% of deterministic use | `electric_propellant_margin` |

Both the dry-mass and electric-propellant-tank constraints are enabled in the
Studio asteroid fixture. With these settings, EMTG requires

```text
arrival mass - 0.10 * deterministic xenon use >= 1,200 kg
1.10 * deterministic xenon use <= 1,000 kg
```

The second relation permits at most 909.1 kg of deterministic xenon use while
retaining the remainder as reserve. A trajectory that consumes 119.66 kg, like
the anomalous solution that motivated this work, must therefore arrive with at
least 1,211.97 kg rather than 3.82 kg.

## Research basis

NASA's closest public system-level concept is a 15 kW Hall-thruster near-Earth
asteroid precursor. Its mass estimate lists 811.6 kg basic dry mass, 973.6 kg
dry mass after 20% growth, 20 kg science payload, 80 kg launch-vehicle adapter,
and 857.2 kg propellant. The study also says that this low-cost technology
demonstrator assumed zero fault tolerance. Excluding the adapter after launch,
that concept is about 994 kg dry with its stated growth and payload. Source:
[Concept Designs for NASA's Solar Electric Propulsion Technology Demonstration
Mission](https://ntrs.nasa.gov/citations/20140017761).

Flight heritage brackets a more flight-like design above that concept:

- Dawn's spacecraft mass was 747.1 kg, in addition to 425 kg xenon and 45.6 kg
  hydrazine, with more than 10 kW available at 1 AU. Source:
  [NASA Dawn spacecraft](https://science.nasa.gov/mission/dawn/technology/spacecraft/).
- Psyche launched at 2,747 kg with up to 1,085 kg xenon and 21 kW near Earth.
  Subtracting xenon leaves 1,662 kg, although that remainder is not a formal dry
  mass and includes DSOC and any other non-xenon items. Source:
  [NASA Psyche spacecraft](https://science.nasa.gov/mission/psyche/spacecraft/).

The 1,200 kg floor is consequently a rounded, cautious system-level reference:
it is above the low-cost 15 kW concept and lies near an interpolation of Dawn
and Psyche at 15 kW. It is deliberately not a claim that every 15 kW spacecraft
must have this mass.

The 10% xenon reserve is likewise an early-phase planning default. A NASA DART
trajectory design allocated 3% of deterministic xenon for operations, 5% for
missed thrust, and about 1% for residuals. Rounding that approximately 9%
allocation to 10% avoids false precision. Source:
[DART Mission Design](https://ntrs.nasa.gov/citations/20170001428).

## Why subsystem mass fields remain zero

The fixture assembles its power and propulsion performance models from EMTG's
libraries. Those generic library entries currently have zero power-system mass
per kW and zero propulsion-system mass per string. The 1,200 kg floor is
explicitly inclusive of those subsystems, so assigning component masses as well
would double-count them.

When a specific flight design supplies a component-level mass equipment list,
the cleaner upgrade is to use an explicit spacecraft options file with a base
bus mass plus nonzero subsystem masses and a stage dry-mass constraint. Until
then, the inclusive global dry-mass constraint is the more honest model and
preserves Studio's ability to select performance records from the hardware
libraries.

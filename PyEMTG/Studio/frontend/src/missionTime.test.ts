import { describe, expect, it } from 'vitest'
import { tdbMinusUtcAt, utcDateFromTdbMjd } from './missionTime'

describe('mission time conversion', () => {
  const anchors = [
    { epoch_mjd: 61300, tdb_minus_utc_seconds: 69.1824273893128 },
    { epoch_mjd: 63887.936283680145, tdb_minus_utc_seconds: 69.18238095795991 },
  ]

  it('converts EMTG TDB MJD to the SPICE UTC instant', () => {
    expect(utcDateFromTdbMjd(61300.0000000298, anchors)?.toISOString())
      .toBe('2026-09-16T23:58:50.820Z')
  })

  it('interpolates the small relativistic offset variation', () => {
    const middle = (anchors[0].epoch_mjd + anchors[1].epoch_mjd) / 2
    expect(tdbMinusUtcAt(middle, anchors)).toBeCloseTo(
      (anchors[0].tdb_minus_utc_seconds + anchors[1].tdb_minus_utc_seconds) / 2,
      10,
    )
  })

  it('does not claim UTC when no leap-second kernel offset is available', () => {
    expect(utcDateFromTdbMjd(61300, [])).toBeUndefined()
  })
})

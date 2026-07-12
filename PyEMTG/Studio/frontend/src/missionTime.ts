import type { TrajectorySample } from './types'

export type TimeAnchor = Pick<TrajectorySample, 'epoch_mjd' | 'tdb_minus_utc_seconds'>

export function tdbMinusUtcAt(epochMjd: number, anchors: TimeAnchor[]): number | undefined {
  const available = anchors
    .filter((anchor): anchor is Required<TimeAnchor> => Number.isFinite(anchor.tdb_minus_utc_seconds))
    .sort((first, second) => first.epoch_mjd - second.epoch_mjd)
  if (!available.length) return undefined
  const upperIndex = available.findIndex(anchor => anchor.epoch_mjd >= epochMjd)
  if (upperIndex <= 0) return available[0].tdb_minus_utc_seconds
  if (upperIndex < 0) return available[available.length - 1].tdb_minus_utc_seconds
  const lower = available[upperIndex - 1]
  const upper = available[upperIndex]
  const span = upper.epoch_mjd - lower.epoch_mjd
  if (span <= 0) return upper.tdb_minus_utc_seconds
  const fraction = (epochMjd - lower.epoch_mjd) / span
  return lower.tdb_minus_utc_seconds
    + (upper.tdb_minus_utc_seconds - lower.tdb_minus_utc_seconds) * fraction
}

export function utcDateFromTdbMjd(epochMjd: number, anchors: TimeAnchor[]): Date | undefined {
  const offset = tdbMinusUtcAt(epochMjd, anchors)
  if (offset === undefined) return undefined
  const utcMjd = epochMjd - offset / 86400
  return new Date((utcMjd - 40587) * 86400000)
}

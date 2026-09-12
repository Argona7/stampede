export interface RadarFilters {
  preset: string
  minWallets: number
  ageMax: number | null // seconds
  stage: 'curve' | 'graduated' | null
  excludeBots: boolean
  mentionsMax: number | null
  qualityMin: number | null
  sort: string
}

export const DEFAULT_RADAR: RadarFilters = { preset: 'under_radar', minWallets: 2, ageMax: 4 * 3600, stage: 'curve', excludeBots: true, mentionsMax: 3, qualityMin: null, sort: 'score' }

export function presetToFilters(k: string): Partial<RadarFilters> {
  switch (k) {
    case 'under_radar':
      return { stage: 'curve', ageMax: 4 * 3600, mentionsMax: 3, excludeBots: true, sort: 'score', qualityMin: null }
    case 'graduating':
      return { stage: 'curve', ageMax: null, mentionsMax: null, sort: 'progress', qualityMin: null }
    case 'smart_rotators':
      return { stage: null, ageMax: null, mentionsMax: null, excludeBots: true, sort: 'quality', qualityMin: 0.55 }
    case 'clean_launch': // bundle ≤ 2, dev buy ≤ 5%, not a launch farm: applied server-side from the preset name
      return { stage: null, ageMax: null, mentionsMax: null, sort: 'score', qualityMin: null }
    default:
      return { stage: null, ageMax: null, mentionsMax: null, sort: 'score', qualityMin: null }
  }
}

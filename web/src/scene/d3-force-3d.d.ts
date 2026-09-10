declare module 'd3-force-3d' {
  export interface SimNode3 {
    index?: number
    x?: number
    y?: number
    z?: number
    vx?: number
    vy?: number
    vz?: number
    fx?: number | null
    fy?: number | null
    fz?: number | null
  }
  export interface Force<N> {
    (alpha: number): void
    initialize?(nodes: N[], random?: () => number, nDim?: number): void
  }
  export interface Simulation<N extends SimNode3, L> {
    tick(iterations?: number): this
    nodes(): N[]
    nodes(nodes: N[]): this
    alpha(): number
    alpha(a: number): this
    alphaDecay(d: number): this
    velocityDecay(d: number): this
    force(name: string): Force<N> | undefined
    force(name: string, force: Force<N> | null): this
    stop(): this
    numDimensions(): number
    numDimensions(n: number): this
  }
  export function forceSimulation<N extends SimNode3, L = unknown>(nodes?: N[], numDimensions?: number): Simulation<N, L>
  export function forceLink<N extends SimNode3, L>(links?: L[]): {
    (alpha: number): void
    id(fn: (d: N) => string): ReturnType<typeof forceLink<N, L>>
    distance(fn: number | ((l: L) => number)): ReturnType<typeof forceLink<N, L>>
    strength(fn: number | ((l: L) => number)): ReturnType<typeof forceLink<N, L>>
    links(): L[]
    initialize?(nodes: N[], random?: () => number, nDim?: number): void
  }
  export function forceManyBody<N extends SimNode3>(): {
    (alpha: number): void
    strength(fn: number | ((d: N) => number)): ReturnType<typeof forceManyBody<N>>
    distanceMax(d: number): ReturnType<typeof forceManyBody<N>>
    initialize?(nodes: N[], random?: () => number, nDim?: number): void
  }
  export function forceCenter<N extends SimNode3>(x?: number, y?: number, z?: number): { (alpha: number): void; strength(s: number): ReturnType<typeof forceCenter<N>>; initialize?(nodes: N[], random?: () => number, nDim?: number): void }
  export function forceCollide<N extends SimNode3>(r?: number | ((d: N) => number)): { (alpha: number): void; radius(fn: number | ((d: N) => number)): ReturnType<typeof forceCollide<N>>; iterations(n: number): ReturnType<typeof forceCollide<N>>; initialize?(nodes: N[], random?: () => number, nDim?: number): void }
  export function forceX<N extends SimNode3>(x?: number | ((d: N) => number)): { (alpha: number): void; strength(s: number | ((d: N) => number)): ReturnType<typeof forceX<N>>; initialize?(nodes: N[], random?: () => number, nDim?: number): void }
  export function forceY<N extends SimNode3>(y?: number | ((d: N) => number)): { (alpha: number): void; strength(s: number | ((d: N) => number)): ReturnType<typeof forceY<N>>; initialize?(nodes: N[], random?: () => number, nDim?: number): void }
  export function forceZ<N extends SimNode3>(z?: number | ((d: N) => number)): { (alpha: number): void; strength(s: number | ((d: N) => number)): ReturnType<typeof forceZ<N>>; initialize?(nodes: N[], random?: () => number, nDim?: number): void }
}

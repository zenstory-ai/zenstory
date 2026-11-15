import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  useSwipeGestures,
  usePinchGesture,
  usePinchZoom,
  SwipeGestureEvent,
  PinchGestureEvent,
} from '../useGestures'

// Helper to create mock touch list
function createMockTouchList(
  touches: Array<{ clientX: number; clientY: number }>
): React.TouchList {
  const touchArray = touches.map((t, index) => ({
    clientX: t.clientX,
    clientY: t.clientY,
    screenX: t.clientX,
    screenY: t.clientY,
    pageX: t.clientX,
    pageY: t.clientY,
    identifier: index,
    target: null as unknown as EventTarget,
  }))

  // Create array-like object with numeric indices
  const touchList = {
    length: touchArray.length,
    item: (index: number) => touchArray[index] || null,
    ...touchArray,
  } as unknown as React.TouchList

  return touchList
}

// Helper to create mock touch event
function createMockTouchEvent(
  type: 'touchstart' | 'touchmove' | 'touchend',
  touches: Array<{ clientX: number; clientY: number }>,
  changedTouches?: Array<{ clientX: number; clientY: number }>
): React.TouchEvent {
  const mockTouches = createMockTouchList(touches)
  const mockChangedTouches = changedTouches
    ? createMockTouchList(changedTouches)
    : mockTouches

  return {
    nativeEvent: {
      touches: mockTouches as unknown as TouchList,
      changedTouches: mockChangedTouches as unknown as TouchList,
    } as TouchEvent,
    touches: mockTouches,
    changedTouches: mockChangedTouches,
    preventDefault: vi.fn(),
    stopPropagation: vi.fn(),
    persist: vi.fn(),
  } as unknown as React.TouchEvent
}

describe('useGestures', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  describe('useSwipeGestures', () => {
    describe('initial state', () => {
      it('returns a bind function', () => {
        const onSwipe = vi.fn()
        const { result } = renderHook(() => useSwipeGestures(onSwipe))

        expect(result.current.bind).toBeDefined()
        expect(typeof result.current.bind).toBe('function')
      })

      it('bind returns touch event handlers', () => {
        const onSwipe = vi.fn()
        const { result } = renderHook(() => useSwipeGestures(onSwipe))

        const handlers = result.current.bind()

        expect(handlers.onTouchStart).toBeDefined()
        expect(handlers.onTouchEnd).toBeDefined()
        expect(typeof handlers.onTouchStart).toBe('function')
        expect(typeof handlers.onTouchEnd).toBe('function')
      })
    })

    describe('swipe detection', () => {
      it('detects swipe right gesture', async () => {
        const onSwipe = vi.fn()
        const { result } = renderHook(() => useSwipeGestures(onSwipe))

        const handlers = result.current.bind()

        // Touch start at x=100
        const startEvent = createMockTouchEvent('touchstart', [{ clientX: 100, clientY: 200 }])
        handlers.onTouchStart(startEvent)

        // Touch end at x=200 (swipe right = 100px)
        const endEvent = createMockTouchEvent('touchend', [], [{ clientX: 200, clientY: 200 }])

        await act(async () => {
          vi.advanceTimersByTime(100) // 100ms duration
          handlers.onTouchEnd(endEvent)
        })

        expect(onSwipe).toHaveBeenCalled()
        const swipeEvent = onSwipe.mock.calls[0][0] as SwipeGestureEvent
        expect(swipeEvent.direction).toBe('right')
        expect(swipeEvent.deltaX).toBe(100)
      })

      it('detects swipe left gesture', async () => {
        const onSwipe = vi.fn()
        const { result } = renderHook(() => useSwipeGestures(onSwipe))

        const handlers = result.current.bind()

        const startEvent = createMockTouchEvent('touchstart', [{ clientX: 200, clientY: 200 }])
        handlers.onTouchStart(startEvent)

        const endEvent = createMockTouchEvent('touchend', [], [{ clientX: 100, clientY: 200 }])

        await act(async () => {
          vi.advanceTimersByTime(100)
          handlers.onTouchEnd(endEvent)
        })

        expect(onSwipe).toHaveBeenCalled()
        const swipeEvent = onSwipe.mock.calls[0][0] as SwipeGestureEvent
        expect(swipeEvent.direction).toBe('left')
        expect(swipeEvent.deltaX).toBe(-100)
      })

      it('detects swipe up gesture when vertical enabled', async () => {
        const onSwipe = vi.fn()
        const { result } = renderHook(() =>
          useSwipeGestures(onSwipe, { enableVerticalSwipe: true })
        )

        const handlers = result.current.bind()

        const startEvent = createMockTouchEvent('touchstart', [{ clientX: 100, clientY: 200 }])
        handlers.onTouchStart(startEvent)

        const endEvent = createMockTouchEvent('touchend', [], [{ clientX: 100, clientY: 100 }])

        await act(async () => {
          vi.advanceTimersByTime(100)
          handlers.onTouchEnd(endEvent)
        })

        expect(onSwipe).toHaveBeenCalled()
        const swipeEvent = onSwipe.mock.calls[0][0] as SwipeGestureEvent
        expect(swipeEvent.direction).toBe('up')
        expect(swipeEvent.deltaY).toBe(-100)
      })

      it('detects swipe down gesture when vertical enabled', async () => {
        const onSwipe = vi.fn()
        const { result } = renderHook(() =>
          useSwipeGestures(onSwipe, { enableVerticalSwipe: true })
        )

        const handlers = result.current.bind()

        const startEvent = createMockTouchEvent('touchstart', [{ clientX: 100, clientY: 100 }])
        handlers.onTouchStart(startEvent)

        const endEvent = createMockTouchEvent('touchend', [], [{ clientX: 100, clientY: 200 }])

        await act(async () => {
          vi.advanceTimersByTime(100)
          handlers.onTouchEnd(endEvent)
        })

        expect(onSwipe).toHaveBeenCalled()
        const swipeEvent = onSwipe.mock.calls[0][0] as SwipeGestureEvent
        expect(swipeEvent.direction).toBe('down')
        expect(swipeEvent.deltaY).toBe(100)
      })

      it('does not trigger callback if swipe is below threshold', async () => {
        const onSwipe = vi.fn()
        const { result } = renderHook(() =>
          useSwipeGestures(onSwipe, { swipeThreshold: 100 })
        )

        const handlers = result.current.bind()

        // Only 50px swipe (below threshold of 100)
        const startEvent = createMockTouchEvent('touchstart', [{ clientX: 100, clientY: 200 }])
        handlers.onTouchStart(startEvent)

        const endEvent = createMockTouchEvent('touchend', [], [{ clientX: 150, clientY: 200 }])

        await act(async () => {
          vi.advanceTimersByTime(100)
          handlers.onTouchEnd(endEvent)
        })

        expect(onSwipe).not.toHaveBeenCalled()
      })

      it('does not trigger callback if swipe exceeds timeout', async () => {
        const onSwipe = vi.fn()
        const { result } = renderHook(() =>
          useSwipeGestures(onSwipe, { swipeTimeout: 200 })
        )

        const handlers = result.current.bind()

        const startEvent = createMockTouchEvent('touchstart', [{ clientX: 100, clientY: 200 }])
        handlers.onTouchStart(startEvent)

        const endEvent = createMockTouchEvent('touchend', [], [{ clientX: 200, clientY: 200 }])

        await act(async () => {
          vi.advanceTimersByTime(500) // 500ms exceeds 200ms timeout
          handlers.onTouchEnd(endEvent)
        })

        expect(onSwipe).not.toHaveBeenCalled()
      })

      it('does not trigger callback if velocity is below threshold', async () => {
        const onSwipe = vi.fn()
        const { result } = renderHook(() =>
          useSwipeGestures(onSwipe, { swipeVelocity: 1 }) // 1 pixel/ms = very fast
        )

        const handlers = result.current.bind()

        // 100px in 500ms = 0.2 px/ms (below threshold of 1)
        const startEvent = createMockTouchEvent('touchstart', [{ clientX: 100, clientY: 200 }])
        handlers.onTouchStart(startEvent)

        const endEvent = createMockTouchEvent('touchend', [], [{ clientX: 200, clientY: 200 }])

        await act(async () => {
          vi.advanceTimersByTime(500)
          handlers.onTouchEnd(endEvent)
        })

        expect(onSwipe).not.toHaveBeenCalled()
      })

      it('does not trigger if no touch start recorded', async () => {
        const onSwipe = vi.fn()
        const { result } = renderHook(() => useSwipeGestures(onSwipe))

        const handlers = result.current.bind()

        // Call touch end without touch start
        const endEvent = createMockTouchEvent('touchend', [], [{ clientX: 200, clientY: 200 }])

        await act(async () => {
          handlers.onTouchEnd(endEvent)
        })

        expect(onSwipe).not.toHaveBeenCalled()
      })
    })

    describe('configuration options', () => {
      it('uses custom swipeThreshold', async () => {
        const onSwipe = vi.fn()
        const { result } = renderHook(() =>
          useSwipeGestures(onSwipe, { swipeThreshold: 30 })
        )

        const handlers = result.current.bind()

        // 50px swipe should trigger with threshold of 30
        const startEvent = createMockTouchEvent('touchstart', [{ clientX: 100, clientY: 200 }])
        handlers.onTouchStart(startEvent)

        const endEvent = createMockTouchEvent('touchend', [], [{ clientX: 150, clientY: 200 }])

        await act(async () => {
          vi.advanceTimersByTime(100)
          handlers.onTouchEnd(endEvent)
        })

        expect(onSwipe).toHaveBeenCalled()
      })

      it('uses custom swipeTimeout', async () => {
        const onSwipe = vi.fn()
        const { result } = renderHook(() =>
          useSwipeGestures(onSwipe, { swipeTimeout: 500 })
        )

        const handlers = result.current.bind()

        const startEvent = createMockTouchEvent('touchstart', [{ clientX: 100, clientY: 200 }])
        handlers.onTouchStart(startEvent)

        const endEvent = createMockTouchEvent('touchend', [], [{ clientX: 300, clientY: 200 }])

        await act(async () => {
          vi.advanceTimersByTime(300) // 200px in 300ms = 0.67 px/ms (exceeds 0.3 threshold)
          handlers.onTouchEnd(endEvent)
        })

        expect(onSwipe).toHaveBeenCalled()
      })

      it('does not detect vertical swipes by default', async () => {
        const onSwipe = vi.fn()
        const { result } = renderHook(() => useSwipeGestures(onSwipe))

        const handlers = result.current.bind()

        const startEvent = createMockTouchEvent('touchstart', [{ clientX: 100, clientY: 100 }])
        handlers.onTouchStart(startEvent)

        const endEvent = createMockTouchEvent('touchend', [], [{ clientX: 100, clientY: 200 }])

        await act(async () => {
          vi.advanceTimersByTime(100)
          handlers.onTouchEnd(endEvent)
        })

        // Vertical swipe should not be detected without enableVerticalSwipe
        expect(onSwipe).not.toHaveBeenCalled()
      })

      it('calls preventDefault when configured', async () => {
        const onSwipe = vi.fn()
        const { result } = renderHook(() =>
          useSwipeGestures(onSwipe, { preventDefaultTouch: true })
        )

        const handlers = result.current.bind()

        const startEvent = createMockTouchEvent('touchstart', [{ clientX: 100, clientY: 200 }])
        handlers.onTouchStart(startEvent)

        const endEvent = createMockTouchEvent('touchend', [], [{ clientX: 200, clientY: 200 }])

        await act(async () => {
          vi.advanceTimersByTime(100)
          handlers.onTouchEnd(endEvent)
        })

        expect(endEvent.preventDefault).toHaveBeenCalled()
      })
    })

    describe('gesture event details', () => {
      it('includes velocity in swipe event', async () => {
        const onSwipe = vi.fn()
        const { result } = renderHook(() => useSwipeGestures(onSwipe))

        const handlers = result.current.bind()

        const startEvent = createMockTouchEvent('touchstart', [{ clientX: 100, clientY: 200 }])
        handlers.onTouchStart(startEvent)

        const endEvent = createMockTouchEvent('touchend', [], [{ clientX: 200, clientY: 200 }])

        await act(async () => {
          vi.advanceTimersByTime(100) // 100px in 100ms = 1 px/ms
          handlers.onTouchEnd(endEvent)
        })

        expect(onSwipe).toHaveBeenCalled()
        const swipeEvent = onSwipe.mock.calls[0][0] as SwipeGestureEvent
        expect(swipeEvent.velocity).toBeCloseTo(1, 1)
      })
    })
  })

  describe('usePinchGesture', () => {
    describe('initial state', () => {
      it('returns a bind function', () => {
        const onPinch = vi.fn()
        const { result } = renderHook(() => usePinchGesture(onPinch))

        expect(result.current.bind).toBeDefined()
      })

      it('bind returns touch event handlers', () => {
        const onPinch = vi.fn()
        const { result } = renderHook(() => usePinchGesture(onPinch))

        const handlers = result.current.bind()

        expect(handlers.onTouchStart).toBeDefined()
        expect(handlers.onTouchMove).toBeDefined()
        expect(handlers.onTouchEnd).toBeDefined()
      })
    })

    describe('pinch detection', () => {
      it('detects pinch out (zoom in)', async () => {
        const onPinch = vi.fn()
        const { result } = renderHook(() => usePinchGesture(onPinch))

        const handlers = result.current.bind()

        // Start with two fingers 100px apart
        const startEvent = createMockTouchEvent('touchstart', [
          { clientX: 100, clientY: 200 },
          { clientX: 200, clientY: 200 },
        ])
        handlers.onTouchStart(startEvent)

        // Move fingers to 200px apart (scale = 2)
        const moveEvent = createMockTouchEvent('touchmove', [
          { clientX: 50, clientY: 200 },
          { clientX: 250, clientY: 200 },
        ])

        await act(async () => {
          handlers.onTouchMove(moveEvent)
        })

        expect(onPinch).toHaveBeenCalled()
        const pinchEvent = onPinch.mock.calls[0][0] as PinchGestureEvent
        expect(pinchEvent.scale).toBeCloseTo(2, 0)
      })

      it('detects pinch in (zoom out)', async () => {
        const onPinch = vi.fn()
        const { result } = renderHook(() => usePinchGesture(onPinch))

        const handlers = result.current.bind()

        // Start with two fingers 200px apart
        const startEvent = createMockTouchEvent('touchstart', [
          { clientX: 50, clientY: 200 },
          { clientX: 250, clientY: 200 },
        ])
        handlers.onTouchStart(startEvent)

        // Move fingers to 100px apart (scale = 0.5)
        const moveEvent = createMockTouchEvent('touchmove', [
          { clientX: 100, clientY: 200 },
          { clientX: 200, clientY: 200 },
        ])

        await act(async () => {
          handlers.onTouchMove(moveEvent)
        })

        expect(onPinch).toHaveBeenCalled()
        const pinchEvent = onPinch.mock.calls[0][0] as PinchGestureEvent
        expect(pinchEvent.scale).toBeCloseTo(0.5, 0)
      })

      it('includes center point in pinch event', async () => {
        const onPinch = vi.fn()
        const { result } = renderHook(() => usePinchGesture(onPinch))

        const handlers = result.current.bind()

        const startEvent = createMockTouchEvent('touchstart', [
          { clientX: 100, clientY: 200 },
          { clientX: 300, clientY: 200 },
        ])
        handlers.onTouchStart(startEvent)

        // Move to trigger pinch with scale change > threshold
        const moveEvent = createMockTouchEvent('touchmove', [
          { clientX: 50, clientY: 200 },
          { clientX: 350, clientY: 200 },
        ])

        await act(async () => {
          handlers.onTouchMove(moveEvent)
        })

        expect(onPinch).toHaveBeenCalled()
        const pinchEvent = onPinch.mock.calls[0][0] as PinchGestureEvent
        expect(pinchEvent.center).toEqual({ x: 200, y: 200 })
      })

      it('resets on touch end', async () => {
        const onPinch = vi.fn()
        const { result } = renderHook(() => usePinchGesture(onPinch))

        const handlers = result.current.bind()

        const startEvent = createMockTouchEvent('touchstart', [
          { clientX: 100, clientY: 200 },
          { clientX: 200, clientY: 200 },
        ])
        handlers.onTouchStart(startEvent)

        const endEvent = createMockTouchEvent('touchend', [])

        await act(async () => {
          handlers.onTouchEnd(endEvent)
        })

        // After touch end, a new pinch should start fresh
        const startEvent2 = createMockTouchEvent('touchstart', [
          { clientX: 100, clientY: 200 },
          { clientX: 200, clientY: 200 },
        ])
        handlers.onTouchStart(startEvent2)

        const moveEvent = createMockTouchEvent('touchmove', [
          { clientX: 50, clientY: 200 },
          { clientX: 250, clientY: 200 },
        ])

        await act(async () => {
          handlers.onTouchMove(moveEvent)
        })

        // Should work as new pinch gesture
        expect(onPinch).toHaveBeenCalledTimes(1)
      })
    })

    describe('threshold configuration', () => {
      it('only triggers when scale change exceeds threshold', async () => {
        const onPinch = vi.fn()
        const { result } = renderHook(() =>
          usePinchGesture(onPinch, { pinchThreshold: 0.5 })
        )

        const handlers = result.current.bind()

        // Start with fingers 100px apart
        const startEvent = createMockTouchEvent('touchstart', [
          { clientX: 100, clientY: 200 },
          { clientX: 200, clientY: 200 },
        ])
        handlers.onTouchStart(startEvent)

        // Small change (110px = scale 1.1, below threshold of 0.5 change)
        const moveEvent1 = createMockTouchEvent('touchmove', [
          { clientX: 95, clientY: 200 },
          { clientX: 205, clientY: 200 },
        ])

        await act(async () => {
          handlers.onTouchMove(moveEvent1)
        })

        expect(onPinch).not.toHaveBeenCalled()

        // Large change (200px = scale 2, exceeds threshold of 0.5)
        const moveEvent2 = createMockTouchEvent('touchmove', [
          { clientX: 50, clientY: 200 },
          { clientX: 250, clientY: 200 },
        ])

        await act(async () => {
          handlers.onTouchMove(moveEvent2)
        })

        expect(onPinch).toHaveBeenCalled()
      })
    })

    describe('edge cases', () => {
      it('does not trigger on single touch', async () => {
        const onPinch = vi.fn()
        const { result } = renderHook(() => usePinchGesture(onPinch))

        const handlers = result.current.bind()

        const startEvent = createMockTouchEvent('touchstart', [{ clientX: 100, clientY: 200 }])
        handlers.onTouchStart(startEvent)

        const moveEvent = createMockTouchEvent('touchmove', [{ clientX: 150, clientY: 200 }])

        await act(async () => {
          handlers.onTouchMove(moveEvent)
        })

        expect(onPinch).not.toHaveBeenCalled()
      })

      it('does not trigger without initial touch start', async () => {
        const onPinch = vi.fn()
        const { result } = renderHook(() => usePinchGesture(onPinch))

        const handlers = result.current.bind()

        const moveEvent = createMockTouchEvent('touchmove', [
          { clientX: 100, clientY: 200 },
          { clientX: 200, clientY: 200 },
        ])

        await act(async () => {
          handlers.onTouchMove(moveEvent)
        })

        expect(onPinch).not.toHaveBeenCalled()
      })
    })
  })

  describe('usePinchZoom', () => {
    describe('initial state', () => {
      it('initializes with default zoom of 1', () => {
        const { result } = renderHook(() => usePinchZoom())

        expect(result.current.zoom).toBe(1)
      })

      it('initializes with custom initial zoom', () => {
        const { result } = renderHook(() => usePinchZoom(1.5))

        expect(result.current.zoom).toBe(1.5)
      })

      it('returns bind function', () => {
        const { result } = renderHook(() => usePinchZoom())

        expect(result.current.bind).toBeDefined()
      })

      it('returns setZoom function', () => {
        const { result } = renderHook(() => usePinchZoom())

        expect(result.current.setZoom).toBeDefined()
        expect(typeof result.current.setZoom).toBe('function')
      })

      it('returns resetZoom function', () => {
        const { result } = renderHook(() => usePinchZoom())

        expect(result.current.resetZoom).toBeDefined()
        expect(typeof result.current.resetZoom).toBe('function')
      })
    })

    describe('zoom control', () => {
      it('updates zoom via setZoom', () => {
        const { result } = renderHook(() => usePinchZoom())

        act(() => {
          result.current.setZoom(1.5)
        })

        expect(result.current.zoom).toBe(1.5)
      })

      it('resets zoom via resetZoom', () => {
        const { result } = renderHook(() => usePinchZoom(1))

        act(() => {
          result.current.setZoom(2)
        })

        expect(result.current.zoom).toBe(2)

        act(() => {
          result.current.resetZoom()
        })

        expect(result.current.zoom).toBe(1)
      })

      it('resets to custom initial zoom', () => {
        const { result } = renderHook(() => usePinchZoom(1.5))

        act(() => {
          result.current.setZoom(2)
        })

        act(() => {
          result.current.resetZoom()
        })

        expect(result.current.zoom).toBe(1.5)
      })
    })

    describe('pinch zoom', () => {
      it('increases zoom on pinch out', async () => {
        const { result } = renderHook(() => usePinchZoom())

        const handlers = result.current.bind()

        // Start pinch
        const startEvent = createMockTouchEvent('touchstart', [
          { clientX: 100, clientY: 200 },
          { clientX: 200, clientY: 200 },
        ])

        await act(async () => {
          handlers.onTouchStart(startEvent)
        })

        // Pinch out (scale = 2)
        const moveEvent = createMockTouchEvent('touchmove', [
          { clientX: 50, clientY: 200 },
          { clientX: 250, clientY: 200 },
        ])

        await act(async () => {
          handlers.onTouchMove(moveEvent)
        })

        expect(result.current.zoom).toBeCloseTo(2, 0)
      })

      it('decreases zoom on pinch in', async () => {
        const { result } = renderHook(() => usePinchZoom())

        const handlers = result.current.bind()

        // Start pinch
        const startEvent = createMockTouchEvent('touchstart', [
          { clientX: 50, clientY: 200 },
          { clientX: 250, clientY: 200 },
        ])

        await act(async () => {
          handlers.onTouchStart(startEvent)
        })

        // Pinch in (scale = 0.5)
        const moveEvent = createMockTouchEvent('touchmove', [
          { clientX: 100, clientY: 200 },
          { clientX: 200, clientY: 200 },
        ])

        await act(async () => {
          handlers.onTouchMove(moveEvent)
        })

        expect(result.current.zoom).toBeCloseTo(0.5, 0)
      })

      it('respects min zoom limit', async () => {
        const { result } = renderHook(() => usePinchZoom(1, 0.5, 2))

        const handlers = result.current.bind()

        const startEvent = createMockTouchEvent('touchstart', [
          { clientX: 100, clientY: 200 },
          { clientX: 200, clientY: 200 },
        ])

        await act(async () => {
          handlers.onTouchStart(startEvent)
        })

        // Try to zoom way out (would be 0.25 without limits)
        const moveEvent = createMockTouchEvent('touchmove', [
          { clientX: 125, clientY: 200 },
          { clientX: 175, clientY: 200 },
        ])

        await act(async () => {
          handlers.onTouchMove(moveEvent)
        })

        expect(result.current.zoom).toBe(0.5) // Min limit
      })

      it('respects max zoom limit', async () => {
        const { result } = renderHook(() => usePinchZoom(1, 0.5, 2))

        const handlers = result.current.bind()

        const startEvent = createMockTouchEvent('touchstart', [
          { clientX: 100, clientY: 200 },
          { clientX: 200, clientY: 200 },
        ])

        await act(async () => {
          handlers.onTouchStart(startEvent)
        })

        // Try to zoom way in (would be 4 without limits)
        const moveEvent = createMockTouchEvent('touchmove', [
          { clientX: 0, clientY: 200 },
          { clientX: 300, clientY: 200 },
        ])

        await act(async () => {
          handlers.onTouchMove(moveEvent)
        })

        expect(result.current.zoom).toBe(2) // Max limit
      })
    })

    describe('cumulative zoom', () => {
      it('accumulates zoom across multiple pinch gestures', async () => {
        const { result } = renderHook(() => usePinchZoom())

        const handlers = result.current.bind()

        // First pinch out (scale = 2)
        const startEvent1 = createMockTouchEvent('touchstart', [
          { clientX: 100, clientY: 200 },
          { clientX: 200, clientY: 200 },
        ])

        await act(async () => {
          handlers.onTouchStart(startEvent1)
        })

        const moveEvent1 = createMockTouchEvent('touchmove', [
          { clientX: 50, clientY: 200 },
          { clientX: 250, clientY: 200 },
        ])

        await act(async () => {
          handlers.onTouchMove(moveEvent1)
        })

        // Store base zoom
        const zoomAfterFirst = result.current.zoom

        // End first pinch
        const endEvent1 = createMockTouchEvent('touchend', [])
        await act(async () => {
          handlers.onTouchEnd(endEvent1)
        })

        // Second pinch out from new base
        const startEvent2 = createMockTouchEvent('touchstart', [
          { clientX: 100, clientY: 200 },
          { clientX: 200, clientY: 200 },
        ])

        await act(async () => {
          handlers.onTouchStart(startEvent2)
        })

        const moveEvent2 = createMockTouchEvent('touchmove', [
          { clientX: 50, clientY: 200 },
          { clientX: 250, clientY: 200 },
        ])

        await act(async () => {
          handlers.onTouchMove(moveEvent2)
        })

        // Zoom should be cumulative (limited by max)
        expect(result.current.zoom).toBe(Math.min(2, zoomAfterFirst * 2))
      })
    })
  })
})

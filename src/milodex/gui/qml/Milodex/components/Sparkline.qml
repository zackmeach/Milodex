// Sparkline.qml — Calm intraday-P&L sparkline.
//
// Used by the FRONT digest (calm variant: no axis labels, no grid) and
// the DESK trading-desk (with grid lines and axis labels).  The same
// component handles both via the `showGrid` and `showAxis` flags.
//
// Implementation: Canvas-based.  QML's Canvas exposes a 2D drawing
// context (HTML5-style) that's perfect for arbitrary path rendering.
// Repaints automatically when `series` or `width` change.
//
// Tokens consumed:
//   color.text.disabled  — baseline (the zero line)
//   color.text.muted     — axis tick labels (when shown)
//   status.positive      — line + area for non-negative final value
//   status.negative      — line + area when terminal value is negative
//   color.border.regular — hairline stroke (when hairline:true)
//
// Public API:
//   series   : array of numbers — the cumulative P&L points
//   showGrid : bool — render dashed grid lines at min/0/max (default false)
//   showAxis : bool — render "09:30" / "NOW" tick labels (default false)
//   areaAlpha: real — area fill alpha (default 0.15; calm variant uses 0.08)
//   hairline : bool — single calm stroke; no area/dot/grid (default false)

import QtQuick
import Milodex 1.0

Item {
    id: root

    // ------------------------------------------------------------------
    // Public API
    // ------------------------------------------------------------------

    property var    series:    []
    property bool   showGrid:  false
    property bool   showAxis:  false
    property real   areaAlpha: 0.15
    property bool   hairline:  false

    implicitWidth:  360
    implicitHeight: 64

    // Repaint whenever inputs change
    onSeriesChanged:    canvas.requestPaint()
    onShowGridChanged:  canvas.requestPaint()
    onShowAxisChanged:  canvas.requestPaint()
    onAreaAlphaChanged: canvas.requestPaint()
    onHairlineChanged:  canvas.requestPaint()
    onWidthChanged:     canvas.requestPaint()
    onHeightChanged:    canvas.requestPaint()

    Canvas {
        id: canvas
        anchors.fill: parent
        antialiasing: true

        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()

            var W = width
            var H = height
            if (root.series.length < 2) return

            var padL = 0
            var padR = root.showAxis ? 32 : 4
            var padT = 6
            var padB = root.showAxis ? 14 : 6

            var innerW = W - padL - padR
            var innerH = H - padT - padB

            // Compute range
            var smax = root.series[0], smin = root.series[0]
            for (var i = 1; i < root.series.length; i++) {
                if (root.series[i] > smax) smax = root.series[i]
                if (root.series[i] < smin) smin = root.series[i]
            }
            if (smax < 0) smax = 0
            if (smin > 0) smin = 0
            var range = (smax - smin) || 1

            function xAt(i) { return padL + (i / (root.series.length - 1)) * innerW }
            function yAt(v) { return padT + (1 - (v - smin) / range) * innerH }
            var yZero = yAt(0)

            var terminal = root.series[root.series.length - 1]
            var isNeg = terminal < 0
            var lineColor = isNeg ? Theme.status.negative : Theme.status.positive
            if (root.hairline) lineColor = Theme.color.border.regular

            // -- Grid lines (optional) --
            if (root.showGrid) {
                ctx.strokeStyle = Theme.color.border.subtle
                ctx.lineWidth = 1
                ctx.setLineDash([2, 4])
                var ys = [yAt(smin), yZero, yAt(smax)]
                // Dedupe close ys
                var seen = {}
                for (var k = 0; k < ys.length; k++) {
                    var key = Math.round(ys[k])
                    if (seen[key]) continue
                    seen[key] = true
                    ctx.beginPath()
                    ctx.moveTo(padL, ys[k])
                    ctx.lineTo(W - padR, ys[k])
                    ctx.stroke()
                }
                ctx.setLineDash([])
            }

            // -- Baseline (zero line) --
            ctx.strokeStyle = Theme.color.text.disabled
            ctx.lineWidth = 1
            ctx.beginPath()
            ctx.moveTo(padL, yZero)
            ctx.lineTo(W - padR, yZero)
            ctx.stroke()

            // -- Area fill --
            if (!root.hairline) {
            ctx.fillStyle = Qt.rgba(lineColor.r, lineColor.g, lineColor.b, root.areaAlpha)
            ctx.beginPath()
            ctx.moveTo(xAt(0), yZero)
            for (var p = 0; p < root.series.length; p++) {
                ctx.lineTo(xAt(p), yAt(root.series[p]))
            }
            ctx.lineTo(xAt(root.series.length - 1), yZero)
            ctx.closePath()
            ctx.fill()
            }

            // -- Line --
            ctx.strokeStyle = lineColor
            ctx.lineWidth = 1.5
            ctx.lineJoin = "round"
            ctx.beginPath()
            ctx.moveTo(xAt(0), yAt(root.series[0]))
            for (var q = 1; q < root.series.length; q++) {
                ctx.lineTo(xAt(q), yAt(root.series[q]))
            }
            ctx.stroke()

            // -- End-point dot --
            var endX = xAt(root.series.length - 1)
            var endY = yAt(terminal)
            if (!root.hairline) {
            ctx.fillStyle = lineColor
            ctx.beginPath()
            ctx.arc(endX, endY, 2.5, 0, Math.PI * 2)
            ctx.fill()
            }

            // -- Axis tick labels --
            if (root.showAxis) {
                // Canvas 2D requires an inline font string; size pegged to
                // Theme.typography.label.xs.size (12px) for token discipline.
                ctx.font = Theme.typography.label.xs.size + "px 'JetBrains Mono'"
                ctx.fillStyle = Theme.color.text.muted
                ctx.textBaseline = "alphabetic"
                ctx.fillText("09:30", padL, H - 2)
                var nowLabel = "NOW"
                ctx.fillText(nowLabel, W - padR - 26, H - 2)
            }
        }
    }
}

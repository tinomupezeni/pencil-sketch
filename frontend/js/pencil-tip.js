// Draws a small pencil silhouette at (x, y), pointing along `angle`
// (radians), on its own overlay canvas -- so it reads as a pencil actively
// moving over the paper rather than lines simply appearing.
export class PencilTip {
  constructor(ctx) {
    this.ctx = ctx;
  }

  clear(width, height) {
    this.ctx.clearRect(0, 0, width, height);
  }

  /**
   * @param {boolean} penDown pen-contact state (independent of playback
   *   state, see js/playback-state.js's PenState) -- when up, the tip is
   *   drawn lifted slightly off the page and dimmed, so pen-up travel and
   *   evaluation pauses are visually distinguishable from active drawing.
   */
  draw(x, y, angle, penDown = true) {
    const ctx = this.ctx;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(angle);
    if (!penDown) {
      ctx.translate(0, -3); // lifted off the page
      ctx.globalAlpha = 0.6;
    }

    // graphite point (leads in the direction of travel)
    ctx.beginPath();
    ctx.moveTo(10, -1.6);
    ctx.lineTo(10, 1.6);
    ctx.lineTo(3, 0);
    ctx.closePath();
    ctx.fillStyle = "#2e2a26";
    ctx.fill();

    // wood cone
    ctx.beginPath();
    ctx.moveTo(3, -3);
    ctx.lineTo(3, 3);
    ctx.lineTo(-3, 1.8);
    ctx.lineTo(-3, -1.8);
    ctx.closePath();
    ctx.fillStyle = "#d9a86c";
    ctx.fill();

    // painted body
    ctx.fillStyle = "#e3bf55";
    ctx.fillRect(-26, -3, 23, 6);

    // small highlight glow at the contact point so the "just drawn" spot pops
    const glow = ctx.createRadialGradient(10, 0, 0, 10, 0, 7);
    glow.addColorStop(0, "rgba(255, 220, 150, 0.55)");
    glow.addColorStop(1, "rgba(255, 220, 150, 0)");
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(10, 0, 7, 0, Math.PI * 2);
    ctx.fill();

    ctx.restore();
  }
}

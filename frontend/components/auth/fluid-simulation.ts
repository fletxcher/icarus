/**
 * A small WebGL2 "stable fluids" simulation: a velocity field that the pointer stirs.
 *
 * Each frame the velocity is advected by itself, given some vorticity back, made divergence-free
 * by a pressure solve (Jacobi iterations). Tracer particles ride the velocity field and are drawn
 * as fading streaks coloured by speed with the viridis map, the same one the CFD plots use.
 * Fields live in float textures, so it needs WebGL2 with EXT_color_buffer_float;
 * `FluidSimulation.create` returns null where that is missing and the page just shows its plain
 * background.
 */

const SIM_RESOLUTION = 128; // velocity grid cells along the short side
const PRESSURE_ITERATIONS = 20;
const VELOCITY_DISSIPATION = 0.2;
const CURL_STRENGTH = 25;
const SPLAT_RADIUS = 0.0035; // fraction of the view width, squared falloff
const SPLAT_FORCE = 5000;
const MAX_DT = 1 / 30;

const PARTICLE_SIDE = 256; // particles are PARTICLE_SIDE squared
const PARTICLE_SIZE = 1.6; // point size in CSS pixels
const PARTICLE_LIFE = [2, 6]; // seconds before a particle is respawned somewhere random
const TRAIL_FADE = 2.2; // per second; higher shortens the streaks
const GAIN = 9; // brightness a particle adds per second, so the look doesn't depend on the frame rate
const SPEED_SCALE = 40; // velocity (grid cells per second) at which the colour is about 63% along

const VERTEX = `#version 300 es
out vec2 vUv;
void main() {
  vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
  vUv = p;
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}`;

const HEADER = `#version 300 es
precision highp float;
precision highp sampler2D;
in vec2 vUv;
out vec4 outColor;
`;

const ADVECT = `${HEADER}
uniform sampler2D uVelocity;
uniform sampler2D uSource;
uniform vec2 uVelocityTexel;
uniform float uDt;
uniform float uDissipation;
void main() {
  vec2 coord = vUv - uDt * texture(uVelocity, vUv).xy * uVelocityTexel;
  outColor = texture(uSource, coord) / (1.0 + uDissipation * uDt);
}`;

const SPLAT = `${HEADER}
uniform sampler2D uTarget;
uniform float uAspect;
uniform vec2 uPoint;
uniform vec3 uColor;
uniform float uRadius;
void main() {
  vec2 p = vUv - uPoint;
  p.x *= uAspect;
  vec3 splat = exp(-dot(p, p) / uRadius) * uColor;
  outColor = vec4(texture(uTarget, vUv).xyz + splat, 1.0);
}`;

const CURL = `${HEADER}
uniform sampler2D uVelocity;
uniform vec2 uTexel;
void main() {
  float l = texture(uVelocity, vUv - vec2(uTexel.x, 0.0)).y;
  float r = texture(uVelocity, vUv + vec2(uTexel.x, 0.0)).y;
  float b = texture(uVelocity, vUv - vec2(0.0, uTexel.y)).x;
  float t = texture(uVelocity, vUv + vec2(0.0, uTexel.y)).x;
  outColor = vec4(0.5 * (r - l - t + b), 0.0, 0.0, 1.0);
}`;

const VORTICITY = `${HEADER}
uniform sampler2D uVelocity;
uniform sampler2D uCurl;
uniform vec2 uTexel;
uniform float uStrength;
uniform float uDt;
void main() {
  float l = texture(uCurl, vUv - vec2(uTexel.x, 0.0)).x;
  float r = texture(uCurl, vUv + vec2(uTexel.x, 0.0)).x;
  float b = texture(uCurl, vUv - vec2(0.0, uTexel.y)).x;
  float t = texture(uCurl, vUv + vec2(0.0, uTexel.y)).x;
  float c = texture(uCurl, vUv).x;
  vec2 force = 0.5 * vec2(abs(t) - abs(b), abs(r) - abs(l));
  force /= length(force) + 0.0001;
  force *= uStrength * c;
  force.y *= -1.0;
  vec2 v = texture(uVelocity, vUv).xy + force * uDt;
  outColor = vec4(clamp(v, -1000.0, 1000.0), 0.0, 1.0);
}`;

const DIVERGENCE = `${HEADER}
uniform sampler2D uVelocity;
uniform vec2 uTexel;
void main() {
  vec2 c = texture(uVelocity, vUv).xy;
  float l = texture(uVelocity, vUv - vec2(uTexel.x, 0.0)).x;
  float r = texture(uVelocity, vUv + vec2(uTexel.x, 0.0)).x;
  float b = texture(uVelocity, vUv - vec2(0.0, uTexel.y)).y;
  float t = texture(uVelocity, vUv + vec2(0.0, uTexel.y)).y;
  // Walls: the velocity reflects at the edges.
  if (vUv.x - uTexel.x < 0.0) l = -c.x;
  if (vUv.x + uTexel.x > 1.0) r = -c.x;
  if (vUv.y - uTexel.y < 0.0) b = -c.y;
  if (vUv.y + uTexel.y > 1.0) t = -c.y;
  outColor = vec4(0.5 * (r - l + t - b), 0.0, 0.0, 1.0);
}`;

const PRESSURE = `${HEADER}
uniform sampler2D uPressure;
uniform sampler2D uDivergence;
uniform vec2 uTexel;
void main() {
  float l = texture(uPressure, vUv - vec2(uTexel.x, 0.0)).x;
  float r = texture(uPressure, vUv + vec2(uTexel.x, 0.0)).x;
  float b = texture(uPressure, vUv - vec2(0.0, uTexel.y)).x;
  float t = texture(uPressure, vUv + vec2(0.0, uTexel.y)).x;
  float d = texture(uDivergence, vUv).x;
  outColor = vec4((l + r + b + t - d) * 0.25, 0.0, 0.0, 1.0);
}`;

const GRADIENT = `${HEADER}
uniform sampler2D uPressure;
uniform sampler2D uVelocity;
uniform vec2 uTexel;
void main() {
  float l = texture(uPressure, vUv - vec2(uTexel.x, 0.0)).x;
  float r = texture(uPressure, vUv + vec2(uTexel.x, 0.0)).x;
  float b = texture(uPressure, vUv - vec2(0.0, uTexel.y)).x;
  float t = texture(uPressure, vUv + vec2(0.0, uTexel.y)).x;
  vec2 v = texture(uVelocity, vUv).xy - vec2(r - l, t - b);
  outColor = vec4(v, 0.0, 1.0);
}`;

const VIRIDIS = `
vec3 viridis(float t) {
  const vec3 c0 = vec3(0.2777273272234177, 0.005407344544966578, 0.3340998053353061);
  const vec3 c1 = vec3(0.1050930431085774, 1.404613529898575, 1.384590162594685);
  const vec3 c2 = vec3(-0.3308618287255563, 0.214847559468213, 0.09509516302823659);
  const vec3 c3 = vec3(-4.634230498983486, -5.799100973351585, -19.33244095627987);
  const vec3 c4 = vec3(6.228269936347081, 14.17993336680509, 56.69055260068105);
  const vec3 c5 = vec3(4.776384997670288, -13.74514537774601, -65.35303263337234);
  const vec3 c6 = vec3(-5.435455855934631, 4.645852612178535, 26.3124352495832);
  t = clamp(t, 0.0, 1.0);
  return c0 + t * (c1 + t * (c2 + t * (c3 + t * (c4 + t * (c5 + t * c6)))));
}`;

// One texel per particle: x, y (0..1 in the view), age and lifetime in seconds.
const PARTICLE_UPDATE = `${HEADER}
uniform sampler2D uParticles;
uniform sampler2D uVelocity;
uniform vec2 uVelocityTexel;
uniform vec2 uLife;
uniform float uDt;
uniform float uTime;
vec2 hash(vec2 p) {
  p = vec2(dot(p, vec2(127.1, 311.7)), dot(p, vec2(269.5, 183.3)));
  return fract(sin(p) * 43758.5453123);
}
void main() {
  vec4 p = texelFetch(uParticles, ivec2(gl_FragCoord.xy), 0);
  vec2 v = texture(uVelocity, p.xy).xy;
  p.xy += uDt * v * uVelocityTexel;
  p.z += uDt;
  if (p.z > p.w || p.x < 0.0 || p.x > 1.0 || p.y < 0.0 || p.y > 1.0) {
    vec2 r = hash(gl_FragCoord.xy + uTime);
    vec2 q = hash(gl_FragCoord.yx - uTime);
    p = vec4(r, 0.0, mix(uLife.x, uLife.y, q.x));
  }
  outColor = p;
}`;

const POINT_VERTEX = `#version 300 es
precision highp float;
precision highp sampler2D;
uniform sampler2D uParticles;
uniform sampler2D uVelocity;
uniform int uSide;
uniform float uSize;
uniform float uSpeedScale;
uniform float uGain;
out vec3 vColour;
${VIRIDIS}
void main() {
  vec4 p = texelFetch(uParticles, ivec2(gl_VertexID % uSide, gl_VertexID / uSide), 0);
  float speed = 1.0 - exp(-length(texture(uVelocity, p.xy).xy) / uSpeedScale);
  // Fade in and out over a particle's life so respawning never pops.
  float life = smoothstep(0.0, 0.3, p.z) * smoothstep(0.0, 0.3, p.w - p.z);
  vColour = viridis(0.05 + 0.95 * speed) * (0.25 + 0.75 * speed) * life * uGain;
  gl_Position = vec4(p.xy * 2.0 - 1.0, 0.0, 1.0);
  gl_PointSize = uSize;
}`;

const POINT_FRAGMENT = `#version 300 es
precision highp float;
in vec3 vColour;
out vec4 outColor;
void main() {
  vec2 d = gl_PointCoord - 0.5;
  float a = smoothstep(0.25, 0.0, dot(d, d));
  outColor = vec4(vColour * a, a);
}`;

const FADE = `${HEADER}
uniform sampler2D uSource;
uniform float uKeep;
void main() {
  outColor = texture(uSource, vUv) * uKeep;
}`;

const DISPLAY = `${HEADER}
uniform sampler2D uTrail;
void main() {
  vec3 c = max(texture(uTrail, vUv).rgb, 0.0);
  c = 1.0 - exp(-c * 1.4); // soft shoulder instead of clipping
  outColor = vec4(c, max(c.r, max(c.g, c.b))); // premultiplied: the page shows through
}`;

type Uniforms = Record<string, WebGLUniformLocation | null>;

interface Program {
  program: WebGLProgram;
  uniforms: Uniforms;
}

interface Target {
  texture: WebGLTexture;
  framebuffer: WebGLFramebuffer;
  width: number;
  height: number;
  texel: [number, number];
}

interface DoubleTarget {
  read: Target;
  write: Target;
  swap(): void;
}

interface Pointer {
  x: number;
  y: number;
  moved: boolean;
  dx: number;
  dy: number;
}

function compile(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader {
  const shader = gl.createShader(type)!;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    throw new Error(gl.getShaderInfoLog(shader) ?? "shader failed to compile");
  }
  return shader;
}

function link(gl: WebGL2RenderingContext, vertex: WebGLShader, fragment: string): Program {
  const program = gl.createProgram()!;
  gl.attachShader(program, vertex);
  gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, fragment));
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    throw new Error(gl.getProgramInfoLog(program) ?? "program failed to link");
  }
  const uniforms: Uniforms = {};
  const count = gl.getProgramParameter(program, gl.ACTIVE_UNIFORMS) as number;
  for (let i = 0; i < count; i++) {
    const name = gl.getActiveUniform(program, i)!.name;
    uniforms[name] = gl.getUniformLocation(program, name);
  }
  return { program, uniforms };
}

function gridSize(resolution: number, aspect: number): [number, number] {
  return aspect >= 1
    ? [Math.round(resolution * aspect), resolution]
    : [resolution, Math.round(resolution / aspect)];
}

export class FluidSimulation {
  private readonly programs: Record<
    | "advect"
    | "splat"
    | "curl"
    | "vorticity"
    | "divergence"
    | "pressure"
    | "gradient"
    | "particles"
    | "points"
    | "fade"
    | "display",
    Program
  >;
  private readonly pointer: Pointer = {
    x: 0.5,
    y: 0.5,
    moved: false,
    dx: 0,
    dy: 0,
  };
  private velocity!: DoubleTarget;
  private trail!: DoubleTarget;
  private particles!: DoubleTarget;
  private pressure!: DoubleTarget;
  private divergence!: Target;
  private curl!: Target;
  private frame = 0;
  private lastTime = 0;
  private elapsed = 0;
  private width = 0;
  private height = 0;
  private disposed = false;

  private constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly gl: WebGL2RenderingContext,
  ) {
    const vertex = compile(gl, gl.VERTEX_SHADER, VERTEX);
    this.programs = {
      advect: link(gl, vertex, ADVECT),
      splat: link(gl, vertex, SPLAT),
      curl: link(gl, vertex, CURL),
      vorticity: link(gl, vertex, VORTICITY),
      divergence: link(gl, vertex, DIVERGENCE),
      pressure: link(gl, vertex, PRESSURE),
      gradient: link(gl, vertex, GRADIENT),
      particles: link(gl, vertex, PARTICLE_UPDATE),
      points: link(gl, compile(gl, gl.VERTEX_SHADER, POINT_VERTEX), POINT_FRAGMENT),
      fade: link(gl, vertex, FADE),
      display: link(gl, vertex, DISPLAY),
    };
    gl.bindVertexArray(gl.createVertexArray());
    this.resize();
  }

  /** The simulation drawing into the canvas, or null if this browser can't run it. */
  static create(canvas: HTMLCanvasElement): FluidSimulation | null {
    const gl = canvas.getContext("webgl2", {
      alpha: true,
      premultipliedAlpha: true,
      antialias: false,
      depth: false,
      stencil: false,
    });
    if (!gl || !gl.getExtension("EXT_color_buffer_float")) return null;
    try {
      return new FluidSimulation(canvas, gl);
    } catch (error) {
      console.warn("fluid simulation unavailable:", error);
      return null;
    }
  }

  start(): void {
    this.lastTime = performance.now();
    const tick = (now: number) => {
      if (this.disposed) return;
      const dt = Math.min((now - this.lastTime) / 1000, MAX_DT);
      this.lastTime = now;
      if (this.canvas.clientWidth > 0) this.step(dt); // hidden below the lg breakpoint
      this.frame = requestAnimationFrame(tick);
    };
    this.frame = requestAnimationFrame(tick);
  }

  /** Stops drawing. The context is left to go with the canvas: losing it here would break a
   * remount onto the same canvas (React strict mode does that in development). */
  dispose(): void {
    this.disposed = true;
    cancelAnimationFrame(this.frame);
  }

  /** Call when the canvas element's size changed. Restarts the fluid from rest. */
  resize(): void {
    const ratio = Math.min(window.devicePixelRatio || 1, 1.5);
    const width = Math.max(1, Math.round(this.canvas.clientWidth * ratio));
    const height = Math.max(1, Math.round(this.canvas.clientHeight * ratio));
    if (width === this.width && height === this.height) return;
    this.width = this.canvas.width = width;
    this.height = this.canvas.height = height;

    const aspect = width / height;
    const [simW, simH] = gridSize(SIM_RESOLUTION, aspect);
    this.velocity = this.createDouble(simW, simH, this.gl.RG16F, this.gl.RG);
    this.pressure = this.createDouble(simW, simH, this.gl.R16F, this.gl.RED);
    this.trail = this.createDouble(width, height, this.gl.RGBA16F, this.gl.RGBA);
    this.particles = this.createParticles();
    this.divergence = this.createTarget(simW, simH, this.gl.R16F, this.gl.RED);
    this.curl = this.createTarget(simW, simH, this.gl.R16F, this.gl.RED);
    this.seed();
  }

  /** Stirs the fluid where the pointer is, in 0..1 coordinates with the origin top left. */
  pointerMoved(x: number, y: number): void {
    const p = this.pointer;
    p.dx = (x - p.x) * (this.width / this.height);
    p.dy = -(y - p.y);
    p.x = x;
    p.y = y;
    p.moved = Math.abs(p.dx) + Math.abs(p.dy) > 0;
  }

  /** Where the pointer starts, so the first move doesn't fling the fluid across the view. */
  pointerAt(x: number, y: number): void {
    this.pointer.x = x;
    this.pointer.y = y;
    this.pointer.moved = false;
  }

  private createTarget(
    w: number,
    h: number,
    internal: number,
    format: number,
    type: number = this.gl.HALF_FLOAT,
    data: ArrayBufferView | null = null,
  ): Target {
    const { gl } = this;
    const texture = gl.createTexture()!;
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, internal, w, h, 0, format, type, data);
    const framebuffer = gl.createFramebuffer()!;
    gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0);
    gl.viewport(0, 0, w, h);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    return { texture, framebuffer, width: w, height: h, texel: [1 / w, 1 / h] };
  }

  private createDouble(w: number, h: number, internal: number, format: number): DoubleTarget {
    const pair: DoubleTarget = {
      read: this.createTarget(w, h, internal, format),
      write: this.createTarget(w, h, internal, format),
      swap() {
        [pair.read, pair.write] = [pair.write, pair.read];
      },
    };
    return pair;
  }

  /** The particles, spread over the view with random ages so they don't all respawn together. */
  private createParticles(): DoubleTarget {
    const { gl } = this;
    const count = PARTICLE_SIDE * PARTICLE_SIDE;
    const data = new Float32Array(count * 4);
    for (let i = 0; i < count; i++) {
      const life = PARTICLE_LIFE[0] + Math.random() * (PARTICLE_LIFE[1] - PARTICLE_LIFE[0]);
      data.set([Math.random(), Math.random(), Math.random() * life, life], i * 4);
    }
    const make = () =>
      this.createTarget(PARTICLE_SIDE, PARTICLE_SIDE, gl.RGBA32F, gl.RGBA, gl.FLOAT, data);
    const pair: DoubleTarget = {
      read: make(),
      write: make(),
      swap() {
        [pair.read, pair.write] = [pair.write, pair.read];
      },
    };
    for (const target of [pair.read, pair.write]) {
      // 32-bit float textures can't be filtered; they are only read with texelFetch.
      gl.bindTexture(gl.TEXTURE_2D, target.texture);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    }
    return pair;
  }

  /** A few opening swirls, so the panel isn't still before the first mouse move. */
  private seed(): void {
    for (let i = 0; i < 4; i++) {
      const angle = Math.random() * Math.PI * 2;
      this.splat(
        0.25 + Math.random() * 0.5,
        0.25 + Math.random() * 0.5,
        Math.cos(angle) * 600,
        Math.sin(angle) * 600,
      );
    }
  }

  private use(program: Program): Uniforms {
    this.gl.useProgram(program.program);
    return program.uniforms;
  }

  private bind(unit: number, target: Target, uniform: WebGLUniformLocation | null): void {
    const { gl } = this;
    gl.activeTexture(gl.TEXTURE0 + unit);
    gl.bindTexture(gl.TEXTURE_2D, target.texture);
    gl.uniform1i(uniform, unit);
  }

  private draw(target: Target | null): void {
    const { gl } = this;
    gl.bindFramebuffer(gl.FRAMEBUFFER, target?.framebuffer ?? null);
    gl.viewport(0, 0, target?.width ?? this.width, target?.height ?? this.height);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  private splat(x: number, y: number, dx: number, dy: number): void {
    const { gl } = this;
    const u = this.use(this.programs.splat);
    gl.uniform1f(u.uAspect, this.width / this.height);
    gl.uniform2f(u.uPoint, x, 1 - y);
    gl.uniform1f(u.uRadius, SPLAT_RADIUS);

    this.bind(0, this.velocity.read, u.uTarget);
    gl.uniform3f(u.uColor, dx, dy, 0);
    this.draw(this.velocity.write);
    this.velocity.swap();
  }

  private step(dt: number): void {
    const { gl, programs } = this;
    gl.disable(gl.BLEND);

    const p = this.pointer;
    if (p.moved) {
      p.moved = false;
      this.splat(p.x, p.y, p.dx * SPLAT_FORCE, p.dy * SPLAT_FORCE);
    }

    const v = this.velocity;
    let u = this.use(programs.curl);
    gl.uniform2f(u.uTexel, ...v.read.texel);
    this.bind(0, v.read, u.uVelocity);
    this.draw(this.curl);

    u = this.use(programs.vorticity);
    gl.uniform2f(u.uTexel, ...v.read.texel);
    this.bind(0, v.read, u.uVelocity);
    this.bind(1, this.curl, u.uCurl);
    gl.uniform1f(u.uStrength, CURL_STRENGTH);
    gl.uniform1f(u.uDt, dt);
    this.draw(v.write);
    v.swap();

    u = this.use(programs.divergence);
    gl.uniform2f(u.uTexel, ...v.read.texel);
    this.bind(0, v.read, u.uVelocity);
    this.draw(this.divergence);

    u = this.use(programs.pressure);
    gl.uniform2f(u.uTexel, ...v.read.texel);
    this.bind(1, this.divergence, u.uDivergence);
    for (let i = 0; i < PRESSURE_ITERATIONS; i++) {
      this.bind(0, this.pressure.read, u.uPressure);
      this.draw(this.pressure.write);
      this.pressure.swap();
    }

    u = this.use(programs.gradient);
    gl.uniform2f(u.uTexel, ...v.read.texel);
    this.bind(0, this.pressure.read, u.uPressure);
    this.bind(1, v.read, u.uVelocity);
    this.draw(v.write);
    v.swap();

    u = this.use(programs.advect);
    gl.uniform2f(u.uVelocityTexel, ...v.read.texel);
    gl.uniform1f(u.uDt, dt);
    this.bind(0, v.read, u.uVelocity);
    this.bind(1, v.read, u.uSource);
    gl.uniform1f(u.uDissipation, VELOCITY_DISSIPATION);
    this.draw(v.write);
    v.swap();

    this.elapsed += dt;
    u = this.use(programs.particles);
    this.bind(0, this.particles.read, u.uParticles);
    this.bind(1, v.read, u.uVelocity);
    gl.uniform2f(u.uVelocityTexel, ...v.read.texel);
    gl.uniform2f(u.uLife, PARTICLE_LIFE[0], PARTICLE_LIFE[1]);
    gl.uniform1f(u.uDt, dt);
    gl.uniform1f(u.uTime, this.elapsed % 1000);
    this.draw(this.particles.write);
    this.particles.swap();

    // The trail fades a little each frame, and this frame's particles are added on top.
    u = this.use(programs.fade);
    this.bind(0, this.trail.read, u.uSource);
    gl.uniform1f(u.uKeep, Math.exp(-TRAIL_FADE * dt));
    this.draw(this.trail.write);

    u = this.use(programs.points);
    this.bind(0, this.particles.read, u.uParticles);
    this.bind(1, v.read, u.uVelocity);
    gl.uniform1i(u.uSide, PARTICLE_SIDE);
    gl.uniform1f(u.uSize, PARTICLE_SIZE * Math.min(window.devicePixelRatio || 1, 1.5));
    gl.uniform1f(u.uSpeedScale, SPEED_SCALE);
    gl.uniform1f(u.uGain, GAIN * dt);
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.trail.write.framebuffer);
    gl.viewport(0, 0, this.trail.write.width, this.trail.write.height);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE);
    gl.drawArrays(gl.POINTS, 0, PARTICLE_SIDE * PARTICLE_SIDE);
    gl.disable(gl.BLEND);
    this.trail.swap();

    u = this.use(programs.display);
    this.bind(0, this.trail.read, u.uTrail);
    this.draw(null);
  }
}

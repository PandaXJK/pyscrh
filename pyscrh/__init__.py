"""
pyscrh
======

A tiny, **Scratch-style** sprite library built on top of pygame, made for
people moving from Scratch to Python.

Every Scratch block you know has a short Python equivalent::

    from pyscrh import Sprite, clickGF

    apple = Sprite("apple.png")
    apple.turnway = "lr"

    def forever():
        apple.move(5)      # move 5 steps in the current direction
        apple.edgeb()      # bounce off the edge

    clickGF()              # start the game

The public API is intentionally small: create a :class:`Sprite`, put what
should repeat forever in a ``forever()`` function, and call :func:`clickGF`
to start. You never need to touch ``dt`` or the game loop. Call
:func:`stop_all` at any point to close the window and quit.
"""

__all__ = [
    "Sprite",
    "clickGF",
    "stop_all",
    "WIDTH",
    "HEIGHT",
]
__version__ = "0.1.8"

import colorsys
import inspect
import math
import random
import sys

import pygame

# --- pygame / window setup (runs on first import) -----------------------
pygame.init()

WIDTH = 960
HEIGHT = 720
_screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("pyscrh")
_clock = pygame.time.Clock()

# All live sprites. Internal; users never touch this.
_sprites = []


def _shift_hue(image, shift_deg):
    """Return a copy of ``image`` with every pixel's hue shifted by degrees."""
    if not shift_deg:
        return image
    w, h = image.get_size()
    out = image.copy()
    frac = shift_deg / 360.0
    for x in range(w):
        for y in range(h):
            r, g, b, a = image.get_at((x, y))
            hue, light, sat = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
            hue = (hue + frac) % 1.0
            nr, ng, nb = colorsys.hls_to_rgb(hue, light, sat)
            out.set_at((x, y), (int(nr * 255), int(ng * 255), int(nb * 255), a))
    return out


def _fisheye(image, amount):
    """Return a copy of ``image`` with a fish-eye (bulge) distortion.

    ``amount`` = 0 gives the original image; positive values bulge outward,
    negative values pinch inward.
    """
    if not amount:
        return image
    w, h = image.get_size()
    if w < 2 or h < 2:
        return image
    out = image.copy()
    cx, cy = w / 2.0, h / 2.0
    k = amount / 100.0
    max_r = math.hypot(cx, cy)
    for y in range(h):
        for x in range(w):
            dx, dy = x - cx, y - cy
            r = math.hypot(dx, dy) / max_r          # 0..1 from centre
            # positive amount: edges compress inward (bulge / fish-eye)
            # negative amount: edges expand outward (pinch)
            f = max(0.0, 1.0 - k * r * r)
            sx = int(cx + dx * f)
            sy = int(cy + dy * f)
            if 0 <= sx < w and 0 <= sy < h:
                out.set_at((x, y), image.get_at((sx, sy)))
    return out


def _whirl(image, degrees):
    """Return a copy of ``image`` swirled around its centre.

    ``degrees`` = 0 gives the original; positive spins clockwise, negative
    counter-clockwise. The twist grows with distance from the centre, so the
    middle stays put and the edges swirl most.
    """
    if not degrees:
        return image
    w, h = image.get_size()
    if w < 2 or h < 2:
        return image
    out = image.copy()
    cx, cy = w / 2.0, h / 2.0
    max_r = math.hypot(cx, cy) or 1.0
    for y in range(h):
        for x in range(w):
            dx, dy = x - cx, y - cy
            r = math.hypot(dx, dy)
            if r < 0.5:
                continue
            ang = math.atan2(dy, dx)
            ang -= math.radians(degrees * (r / max_r))
            sx = int(cx + r * math.cos(ang))
            sy = int(cy + r * math.sin(ang))
            if 0 <= sx < w and 0 <= sy < h:
                out.set_at((x, y), image.get_at((sx, sy)))
    return out


def _pixelate(image, block):
    """Return a copy of ``image`` pixelated into ``block``-sized squares."""
    block = int(block)
    if block <= 1:
        return image
    w, h = image.get_size()
    out = image.copy()
    for by in range(0, h, block):
        for bx in range(0, w, block):
            color = image.get_at((bx, by))
            x2, y2 = min(bx + block, w), min(by + block, h)
            for y in range(by, y2):
                for x in range(bx, x2):
                    out.set_at((x, y), color)
    return out


def _mosaic(image, amount):
    """Return a copy of ``image`` tiled into an n x n grid of smaller copies.

    ``amount`` = 0 gives the original; each side is split into
    ``1 + round(amount / 10)`` tiles (25 -> 4x4, 50 -> 6x6, 100 -> 11x11).
    """
    if not amount:
        return image
    n = 1 + int(math.floor(amount / 10.0 + 0.5))   # round half-up (like Scratch)
    if n <= 1:
        return image
    w, h = image.get_size()
    tw, th = max(1, w // n), max(1, h // n)
    small = pygame.transform.smoothscale(image, (tw, th))
    out = pygame.Surface((w, h), pygame.SRCALPHA)
    for i in range(n):
        for j in range(n):
            out.blit(small, (i * tw, j * th))
    return out


def _brightness(image, b):
    """Return a copy of ``image`` brightened (b>0) or darkened (b<0).

    ``0`` = original; ``100`` fades fully to white, ``-100`` to black.
    """
    if not b:
        return image
    t = b / 100.0
    w, h = image.get_size()
    out = image.copy()
    for x in range(w):
        for y in range(h):
            r, g, bb, a = image.get_at((x, y))
            if t > 0:
                r += (255 - r) * t
                g += (255 - g) * t
                bb += (255 - bb) * t
            else:
                k = 1.0 + t
                r, g, bb = r * k, g * k, bb * k
            out.set_at((x, y), (int(r), int(g), int(bb), a))
    return out


def _ghost(image, g):
    """Return a copy of ``image`` made transparent.

    ``0`` = solid; ``100`` fades fully to invisible.
    """
    if not g:
        return image
    factor = 1.0 - g / 100.0
    w, h = image.get_size()
    out = image.copy()
    for x in range(w):
        for y in range(h):
            r, gg, bb, a = image.get_at((x, y))
            out.set_at((x, y), (r, gg, bb, int(a * factor)))
    return out


class Sprite:
    """A single game character, loaded from an image file.

    Parameters
    ----------
    image_path : str
        Full path to the sprite image (PNG with transparency works best).
    name : str, optional
        A friendly name. Not required, but handy when you have many sprites.

    Attributes
    ----------
    x, y : float
        Center position of the sprite (read or write directly).
    angle : float
        Direction in degrees: ``0`` points right, ``90`` points down.
    turnway : str
        How the sprite behaves at the screen edge:
        ``"r"`` rotates to bounce, ``"lr"`` flips left-right,
        ``"n"`` does not bounce at all.
    """

    def __init__(self, image_path, name=""):
        # Costumes (looks): a list of (name, surface). The first one is the
        # image given here. switchcostume() picks which one is shown.
        base = pygame.image.load(image_path).convert_alpha()
        self.costumes = [("costume1", base)]
        self._image = base            # the currently shown image
        self.width = base.get_width()
        self.height = base.get_height()

        # Center position
        self._x = self.width / 2
        self._y = self.height / 2

        # Appearance
        self._size = 100.0     # percent: 100 = original size
        self._color = 0        # color effect: hue shift, 200 = full circle
        self._fisheye = 0      # fisheye effect: 0 = none
        self._whirl = 0        # whirl effect (degrees): 0 = none
        self._pix = 0          # pixelate effect: 0 = none
        self._mos = 0          # mosaic effect: 0 = none
        self._bri = 0          # brightness effect: 0 = none
        self._ghost = 0        # ghost (transparency) effect: 0 = none
        self._visible = True   # show() / hide()

        # Movement state
        self.angle = 0
        self.direction_x = 1   # flipped by edgeb on left/right hits
        self.direction_y = 1   # flipped by edgeb on top/bottom hits
        self.flip_x = False    # extra manual horizontal flip
        self.flip_y = False    # extra manual vertical flip
        self._turnway = "r"    # 'r' rotate / 'lr' flip left-right / 'n' none
        self.name = name

        # Gliding state (internal, driven by glidexy / glideto)
        self.glide_target_x = 0
        self.glide_target_y = 0
        self.glide_delta_x = 0
        self.glide_delta_y = 0
        self.glide_distance = 0.0
        self.glide_speed = 0.0
        self.gliding = False

        _sprites.append(self)
        self._layer = len(_sprites) - 1   # higher = drawn in front

    # ------------------------------------------------------------------
    # Position (read / write like a normal attribute, via property)
    # ------------------------------------------------------------------
    @property
    def x(self):
        """Center x of the sprite. Read or write directly, e.g. ``apple.x``."""
        return self._x

    @x.setter
    def x(self, value):
        self._x = value

    @property
    def y(self):
        """Center y of the sprite. Read or write directly, e.g. ``apple.y``."""
        return self._y

    @y.setter
    def y(self, value):
        self._y = value

    @property
    def turnway(self):
        """How the sprite's *image* faces (never changes the real direction).

        ``turnway`` only controls which way the picture points. It never
        touches the actual direction (``angle`` / ``d``) — it just decides
        how the image is drawn, and it always wins over anything else.

        - ``"r"`` — rotate the image to match the sprite's angle.
        - ``"lr"`` — flip the image left/right so it faces the direction
          disc: left half of the disc → faces left, right half → faces
          right. The real direction is untouched.
        - ``"n"`` — never turn the image, no rotation, no flip.
        """
        return self._turnway

    @turnway.setter
    def turnway(self, mode):
        if mode in ("lr", "r", "n"):
            self._turnway = mode

    @property
    def d(self):
        """Direction in degrees (0 = right, 90 = down). Alias for ``angle``."""
        return self.angle

    @d.setter
    def d(self, degrees):
        self.angle = degrees

    @property
    def size(self):
        """Scale of the sprite in percent: ``100`` is original size, ``50``
        is half, ``200`` doubles it. Read or write directly, e.g.
        ``apple.size = 50``.
        """
        return self._size

    @size.setter
    def size(self, value):
        if value > 0:
            self._size = value

    @property
    def color(self):
        """Colour effect: shifts the hue of the whole sprite image.

        Works like Scratch's *color* effect — ``100`` is half a colour
        circle, ``200`` is a full circle (same as ``0``). Read or change it
        directly, e.g. ``cat.color = 5`` or ``cat.color += 5``.
        """
        return self._color

    @color.setter
    def color(self, value):
        self._color = value

    @property
    def fisheye(self):
        """Fisheye effect: bulges the image like a fish-eye lens.

        Works like Scratch's *fisheye* effect. ``0`` = none, ``100`` = a
        strong bulge. Negative values pinch inward instead. Read or change it
        directly, e.g. ``cat.fisheye = 50``.
        """
        return self._fisheye

    @fisheye.setter
    def fisheye(self, value):
        self._fisheye = value

    @property
    def whirl(self):
        """Whirl effect: swirls the image around its centre.

        Works like Scratch's *whirl* effect. ``0`` = none, positive values
        spin it clockwise, negative counter-clockwise (in degrees). Read or
        change it directly, e.g. ``cat.whirl = 30``.
        """
        return self._whirl

    @whirl.setter
    def whirl(self, value):
        self._whirl = value

    @property
    def pix(self):
        """Pixelate effect: turns the image into big square pixels.

        Works like Scratch's *pixelate* effect. ``0`` = none; the value is
        the pixel-block size — ``5`` makes 5x5 blocks. Read or change it
        directly, e.g. ``cat.pix = 8``.
        """
        return self._pix

    @pix.setter
    def pix(self, value):
        self._pix = value

    @property
    def mos(self):
        """Mosaic effect: tiles the image into a grid of smaller copies.

        Works like Scratch's *mosaic* effect. ``0`` = none, ``25`` = a 4x4
        grid, ``50`` = 6x6, ``100`` = 11x11. Read or change it directly,
        e.g. ``cat.mos = 50``.
        """
        return self._mos

    @mos.setter
    def mos(self, value):
        self._mos = value

    @property
    def bri(self):
        """Brightness effect: makes the image brighter or darker.

        Works like Scratch's *brightness* effect. ``0`` = normal, ``100`` =
        fully white, ``-100`` = fully black. Read or change it directly,
        e.g. ``cat.bri = 50``.
        """
        return self._bri

    @bri.setter
    def bri(self, value):
        self._bri = value

    @property
    def ghost(self):
        """Ghost effect: makes the image see-through.

        Works like Scratch's *ghost* effect. ``0`` = solid, higher values =
        more transparent, ``100`` = invisible. Read or change it directly,
        e.g. ``cat.ghost = 60``.
        """
        return self._ghost

    @ghost.setter
    def ghost(self, value):
        self._ghost = value

    def cleareff(self):
        """Clear all graphic effects back to ``0``.

        Resets :attr:`color`, :attr:`fisheye`, :attr:`whirl`, :attr:`pix`,
        :attr:`mos`, :attr:`bri` and :attr:`ghost` to ``0``. ``size`` is not
        touched. Equivalent to Scratch's *clear graphic effects*.
        """
        self._color = 0
        self._fisheye = 0
        self._whirl = 0
        self._pix = 0
        self._mos = 0
        self._bri = 0
        self._ghost = 0

    def show(self):
        """Show the sprite (make it visible again). Equivalent to Scratch's
        *show* block. The sprite keeps moving and acting as usual — it was
        just hidden.
        """
        self._visible = True

    def hide(self):
        """Hide the sprite (make it invisible on screen).

        Equivalent to Scratch's *hide* block. The sprite still moves and
        acts as usual — it's just not drawn. Call :meth:`show` to bring it
        back.
        """
        self._visible = False

    def gotol(self, m):
        """Set the sprite's layer (drawing order).

        ``"front"`` moves it to the very front (drawn on top of everything),
        ``"back"`` to the very back (drawn behind everything). Equivalent to
        Scratch's *go to front/back layer* blocks.
        """
        if m == "front":
            self._layer = max((s._layer for s in _sprites), default=0) + 1
        elif m == "back":
            self._layer = min((s._layer for s in _sprites), default=0) - 1

    def gofl(self, step):
        """Move this sprite's layer forward by ``step`` (draw it closer to
        the front). ``step`` may be negative to move it backward. Equivalent
        to Scratch's *go forward layers* block.
        """
        self._layer += step

    def gobl(self, step):
        """Move this sprite's layer backward by ``step`` (draw it further
        behind). ``step`` may be negative to move it forward. Equivalent to
        Scratch's *go back layers* block.
        """
        self._layer -= step

    # ------------------------------------------------------------------
    # Movement
    # ------------------------------------------------------------------
    def move(self, steps):
        """Walk ``steps`` pixels in the current direction.

        Call this once per frame inside your ``update(dt)`` and the sprite
        keeps walking. Equivalent to Scratch's *move 10 steps*.
        """
        rad = math.radians(self.angle)
        self.x += self.direction_x * steps * math.cos(rad)
        self.y += self.direction_y * steps * math.sin(rad)

    def turnright(self, degrees):
        """Turn right by ``degrees``."""
        self.angle += degrees

    def turnleft(self, degrees):
        """Turn left by ``degrees``."""
        self.angle -= degrees

    def gotoxy(self, x, y):
        """Jump instantly to position ``(x, y)``."""
        self.x = x
        self.y = y

    # ------------------------------------------------------------------
    # Costumes (looks)
    # ------------------------------------------------------------------
    def addcos(self, image_path, name):
        """Add a new costume (look) to the sprite.

        Parameters
        ----------
        image_path : str
            Full path to the costume image.
        name : str
            A name for this costume, used later with :meth:`switchcos`.
            Equivalent to Scratch's *add costume*.
        """
        surf = pygame.image.load(image_path).convert_alpha()
        self.costumes.append((name, surf))

    def switchcos(self, name):
        """Switch to the costume called ``name``.

        The image on screen changes to that costume (the real direction and
        position are untouched). Equivalent to Scratch's *switch costume to*.
        If no costume has that name, nothing happens.
        """
        for costume_name, surface in self.costumes:
            if costume_name == name:
                self._image = surface
                self.width = surface.get_width()
                self.height = surface.get_height()
                return

    def nextcos(self):
        """Switch to the next costume in the list.

        If already on the last costume, wrap around to the first one.
        Equivalent to Scratch's *next costume*.
        """
        if not self.costumes:
            return
        for i, (costume_name, surface) in enumerate(self.costumes):
            if surface is self._image:
                nxt = self.costumes[(i + 1) % len(self.costumes)]
                self.switchcos(nxt[0])
                return
        # current image not in the list — fall back to the first costume
        self.switchcos(self.costumes[0][0])

    @property
    def cosnum(self):
        """The number (index) of the costume currently shown.

        The first costume is ``0``, the second ``1``, and so on. Read it
        directly, e.g. ``print(cat.cosnum)``. Equivalent to Scratch's
        *costume number*.
        """
        for i, (_, surface) in enumerate(self.costumes):
            if surface is self._image:
                return i
        return 0

    @property
    def cosname(self):
        """The name of the costume currently shown.

        Read it directly, e.g. ``print(cat.cosname)``. Equivalent to
        Scratch's *costume name*.
        """
        for name, surface in self.costumes:
            if surface is self._image:
                return name
        return self.costumes[0][0] if self.costumes else None

    def _find(self, name):
        """Look up another sprite by its name. Internal."""
        for sprite in _sprites:
            if sprite.name == name:
                return sprite
        return None

    def goto(self, target):
        """Jump instantly to a target.

        Parameters
        ----------
        target : str
            ``"r"`` for a random spot, ``"m"`` for the mouse position,
            or another sprite's name (e.g. ``"apple"``) to jump on top of
            that sprite.
        """
        if target == "r":
            self.gotoxy(random.randint(0, WIDTH), random.randint(0, HEIGHT))
        elif target == "m":
            self.gotoxy(*pygame.mouse.get_pos())
        else:
            other = self._find(target)
            if other is not None:
                self.gotoxy(other.x, other.y)

    # ------------------------------------------------------------------
    # Gliding
    # ------------------------------------------------------------------
    def glidexy(self, seconds, x, y):
        """Glide smoothly to ``(x, y)`` over ``seconds`` seconds."""
        if seconds <= 0:
            return
        distance = math.hypot(x - self.x, y - self.y)
        if distance < 1e-9:
            self.gliding = False
            return
        self.glide_target_x = x
        self.glide_target_y = y
        self.glide_delta_x = x - self.x
        self.glide_delta_y = y - self.y
        self.glide_distance = distance
        self.glide_speed = distance / seconds
        self.gliding = True

    def glideto(self, seconds, target):
        """Glide smoothly over ``seconds`` seconds.

        Parameters
        ----------
        seconds : float
            How long the glide takes.
        target : str
            ``"r"`` for a random spot, ``"m"`` for the mouse position,
            or another sprite's name to glide to that sprite.
        """
        if target == "r":
            self.glidexy(seconds,
                         random.randint(0, WIDTH),
                         random.randint(0, HEIGHT))
        elif target == "m":
            self.glidexy(seconds, *pygame.mouse.get_pos())
        else:
            other = self._find(target)
            if other is not None:
                self.glidexy(seconds, other.x, other.y)

    # ------------------------------------------------------------------
    # Direction
    # ------------------------------------------------------------------
    def point(self, degrees):
        """Set the direction to ``degrees`` (0 = right, 90 = down)."""
        self.angle = degrees

    def pointtow(self, target):
        """Point towards something.

        Parameters
        ----------
        target : str
            ``"r"`` for a random direction, ``"m"`` to face the mouse,
            or another sprite's name to face that sprite.
        """
        if target == "r":
            self.angle = random.randint(0, 360)
        elif target == "m":
            mouse_x, mouse_y = pygame.mouse.get_pos()
            self.angle = math.degrees(math.atan2(
                mouse_y - self.y, mouse_x - self.x))
        else:
            other = self._find(target)
            if other is not None:
                self.angle = math.degrees(math.atan2(
                    other.y - self.y, other.x - self.x))

    # ------------------------------------------------------------------
    # Edge bouncing
    # ------------------------------------------------------------------
    def edgeb(self):
        """Bounce off the screen edge.

        Equivalent to Scratch's *if on edge, bounce*. The horizontal /
        vertical movement direction flips at the edge. This is about
        movement only — it works no matter what :attr:`turnway` is.
        """
        left = self.x - self.width / 2
        right = self.x + self.width / 2
        top = self.y - self.height / 2
        bottom = self.y + self.height / 2

        if right > WIDTH:
            self.x = WIDTH - self.width / 2
            self.direction_x = -self.direction_x
        elif left < 0:
            self.x = self.width / 2
            self.direction_x = -self.direction_x

        if bottom > HEIGHT:
            self.y = HEIGHT - self.height / 2
            self.direction_y = -self.direction_y
            if self.turnway == "r":
                self.flip_y = not self.flip_y
        elif top < 0:
            self.y = self.height / 2
            self.direction_y = -self.direction_y
            if self.turnway == "r":
                self.flip_y = not self.flip_y

    # ------------------------------------------------------------------
    # Internal per-frame hooks (called by clickGF, not by you)
    # ------------------------------------------------------------------
    def _update(self, dt):
        """Advance an in-progress glide. Regular movement is done by move()."""
        if not self.gliding:
            return
        remaining = math.hypot(self.glide_target_x - self.x,
                               self.glide_target_y - self.y)
        step = self.glide_speed * dt
        if remaining <= step:
            self.x = self.glide_target_x
            self.y = self.glide_target_y
            self.gliding = False
        else:
            self.x += self.glide_delta_x / self.glide_distance * step
            self.y += self.glide_delta_y / self.glide_distance * step

    def _draw(self, surface):
        """Render this sprite onto ``surface``."""
        if not self._visible:
            return
        image = self._image
        if self._size != 100.0:
            factor = self._size / 100.0
            image = pygame.transform.scale(
                image,
                (int(image.get_width() * factor),
                 int(image.get_height() * factor)),
            )
        if self._color:
            # color effect: hue shift. 200 = full circle (= 0 again).
            image = _shift_hue(image, (self._color % 200) / 200.0 * 360.0)
        if self._fisheye:
            image = _fisheye(image, self._fisheye)
        if self._whirl:
            image = _whirl(image, self._whirl)
        if self._pix:
            image = _pixelate(image, self._pix)
        if self._mos:
            image = _mosaic(image, self._mos)
        if self._bri:
            image = _brightness(image, self._bri)
        if self._ghost:
            image = _ghost(image, self._ghost)
        if self.turnway == "r":
            # Rotate the image to match the sprite's real direction.
            image = pygame.transform.rotate(image, self.angle)
        elif self.turnway == "lr":
            # Flip the image left/right based on the direction disc.
            # Left half of the disc (cos < 0) -> faces left; also respect an
            # edge-bounce flip on direction_x. The real angle stays untouched.
            facing_left = (math.cos(math.radians(self.angle))
                           * self.direction_x) < 0
            if facing_left:
                image = pygame.transform.flip(image, True, False)
        # turnway == "n": the image never turns — no rotation, no flip.
        # Extra manual flips still apply on top if you set them yourself.
        if self.flip_x:
            image = pygame.transform.flip(image, True, False)
        if self.flip_y:
            image = pygame.transform.flip(image, False, True)
        rect = image.get_rect(center=(self.x, self.y))
        surface.blit(image, rect)


def clickGF():
    """Start the game loop.

    It automatically looks up ``forever()`` in the module that called it and
    calls it once every frame, like Scratch's *forever* block. You don't
    need to know about ``dt`` — the library handles timing internally.

    An optional ``draw()`` is also called every frame, *after* all sprites
    are drawn, if you want extra drawing. Close the window to quit, or call
    :func:`stop_all` to quit from code.
    """
    frame = inspect.currentframe()
    caller_globals = frame.f_back.f_globals
    del frame
    user_forever = caller_globals.get("forever")
    user_draw = caller_globals.get("draw")

    running = True
    while running:
        dt = _clock.tick(60) / 1000  # seconds since last frame (internal use)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

        if user_forever is not None:
            user_forever()
        for sprite in _sprites:
            sprite._update(dt)

        _screen.fill((0, 0, 0))
        for sprite in sorted(_sprites, key=lambda s: s._layer):
            sprite._draw(_screen)
        if user_draw is not None:
            user_draw()
        pygame.display.flip()

    pygame.quit()


def stop_all():
    """Close the window and stop the program.

    Equivalent to Scratch's *stop all* block. Call it from anywhere, e.g.
    inside ``forever()`` when your game logic says the program should end.
    """
    pygame.quit()
    sys.exit()

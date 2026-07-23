import { useEffect, useState } from 'react';
import type {
  AmbientMotionMode,
  ThemeId,
} from '../domain/bookflow-contract';

interface ThemeEnvironmentProps {
  theme: ThemeId;
  motion: AmbientMotionMode;
  reducedMotion: boolean;
}

const PARTICLES = Array.from({ length: 10 }, (_, index) => index + 1);

export function ThemeEnvironment({
  theme,
  motion,
  reducedMotion,
}: ThemeEnvironmentProps) {
  const [paused, setPaused] = useState(
    () => typeof document !== 'undefined' && document.hidden,
  );
  const effectiveMotion =
    reducedMotion && motion === 'full' ? 'reduced' : motion;

  useEffect(() => {
    const pause = () => setPaused(true);
    const resume = () => setPaused(document.hidden);
    const onVisibilityChange = () => setPaused(document.hidden);
    window.addEventListener('blur', pause);
    window.addEventListener('focus', resume);
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => {
      window.removeEventListener('blur', pause);
      window.removeEventListener('focus', resume);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, []);

  return (
    <div
      className="theme-environment"
      data-testid="theme-environment"
      data-environment={theme}
      data-motion={effectiveMotion}
      data-paused={paused ? 'true' : 'false'}
      aria-hidden="true"
    >
      <div className="theme-background-layer" data-layer="theme-background" data-layer-index="1">
        <span className="environment-depth depth-a" data-layer-index="2" />
        <span className="environment-depth depth-b" data-layer-index="3" />
      </div>
      <div
        className="ambient-motion-layer"
        data-testid="ambient-motion-layer"
        data-layer="ambient-motion"
        data-layer-index="5"
      >
        <span className="ambient-glow glow-a" />
        <span className="ambient-glow glow-b" />
        {PARTICLES.map((particle) => (
          <i
            className={`ambient-particle particle-${particle}`}
            key={particle}
          />
        ))}
        <span className="atlas-sand-haze haze-a" data-testid="atlas-sand-haze" />
        <span className="atlas-sand-haze haze-b" />
      </div>
      <div className="theme-decor-layer" data-layer="theme-decor" data-layer-index="4">
        <span className="decor-window-grid" />
        <span className="decor-branch branch-a" />
        <span className="decor-branch branch-b" />
        <span className="decor-map-route route-a" />
        <span className="decor-map-route route-b" />
        <span className="decor-compass"><b>N</b><i /></span>
        <span className="decor-paperclip" />
        <span className="decor-stamp">EXPEDITION</span>
      </div>
    </div>
  );
}

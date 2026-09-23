export default function SplashScreen({ onContinue }) {
  return (
    <div className="mavis-splash" onClick={onContinue}>
      <div className="mavis-splash__word">MAVIS</div>
      <div className="mavis-splash__tag">AI Flight Instructor</div>
      <div className="mavis-splash__hint">Click anywhere to continue</div>
    </div>
  );
}

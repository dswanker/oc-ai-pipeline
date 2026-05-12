// src/components/LoadScreen.jsx
export function LoadScreen({ error }) {
  return (
    <div className="load-screen">
      <img className="logo" style={{ width: 48, height: 48, fontSize: 16 }} src="/oc-swoosh.svg" alt="OpenClinica" />
      {error ? (
        <>
          <div className="load-err">⚠ {error}</div>
          <div className="load-hint">
            Use URL params: <code style={{ color: "#0ea5e9" }}>?item=ITEM_ID&token=YOUR_TOKEN</code>
            <br />or add <code style={{ color: "#0ea5e9" }}>?demo=true</code> to view sample data.
          </div>
        </>
      ) : (
        <>
          <div className="load-spinner" />
          <div className="load-msg">Loading study spec from Monday.com…</div>
        </>
      )}
    </div>
  );
}
export default LoadScreen;

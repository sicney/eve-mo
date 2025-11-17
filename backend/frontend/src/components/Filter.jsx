export default function Filters({ minVolume, setMinVolume, limit, setLimit }) {
  return (
    <div className="filters">
      <label>
        Min Volume:
        <input
          type="number"
          value={minVolume}
          onChange={(e) => setMinVolume(e.target.value)}
        />
      </label>
      <label>
        Limit:
        <input
          type="number"
          value={limit}
          onChange={(e) => setLimit(e.target.value)}
        />
      </label>
    </div>
  );
}

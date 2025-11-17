import { useState } from "react";
import ItemChart from "./ItemChart";

export default function ItemTable({ items }) {
  const [selected, setSelected] = useState(null);

  return (
    <div className="table-container">
      <table>
        <thead>
          <tr>
            <th>Item</th>
            <th>Avg Price</th>
            <th>True Value</th>
            <th>z-score</th>
            <th>Volume</th>
            <th>% diff</th>
          </tr>
        </thead>

        <tbody>
          {items.map((i) => (
            <tr key={i.type_id} onClick={() => setSelected(i)}>
              <td>{i.type_name}</td>
              <td>{i.average.toFixed(2)}</td>
              <td>{i.rolling_mean?.toFixed(2)}</td>
              <td>{i.z_score?.toFixed(2)}</td>
              <td>{i.volume}</td>
              <td>{(i.pct_diff * 100).toFixed(2)}%</td>
            </tr>
          ))}
        </tbody>
      </table>

      {selected && <ItemChart item={selected} />}
    </div>
  );
}

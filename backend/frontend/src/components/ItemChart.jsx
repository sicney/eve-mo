import {
  LineChart,
  Line,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
} from "recharts";

export default function ItemChart({ item }) {
  return (
    <div>
      <h3>{item.type_name} — Price Chart</h3>
      <LineChart width={800} height={250} data={item.history}>
        <Line type="monotone" dataKey="average" stroke="#8884d8" />
        <Line type="monotone" dataKey="rolling_mean" stroke="#82ca9d" />
        <CartesianGrid stroke="#ccc" />
        <XAxis dataKey="date" />
        <YAxis />
        <Tooltip />
      </LineChart>
    </div>
  );
}

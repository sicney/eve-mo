import { useEffect, useState } from "react";
import { fetchUndervalued } from "./api/api";
import Header from "./components/Header";
import Filters from "./components/Filters";
import ItemTable from "./components/ItemTable";

function App() {
  const [items, setItems] = useState([]);
  const [minVolume, setMinVolume] = useState(50);
  const [limit, setLimit] = useState(50);

  useEffect(() => {
    fetchUndervalued(minVolume, limit).then(setItems);
  }, [minVolume, limit]);

  return (
    <div>
      <Header />
      <Filters
        minVolume={minVolume}
        setMinVolume={setMinVolume}
        limit={limit}
        setLimit={setLimit}
      />
      <ItemTable items={items} />
    </div>
  );
}

export default App;

import axios from "axios";

const api = axios.create({
  baseURL: "/api",
});

export const fetchUndervalued = async (minVolume, limit) => {
  const res = await api.get("/undervalued", {
    params: { min_volume: minVolume, limit },
  });
  return res.data;
};

export default api;
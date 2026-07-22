import { createVercelConfig } from "./deployment-config";

export default createVercelConfig(process.env.VITE_API_BASE_URL);

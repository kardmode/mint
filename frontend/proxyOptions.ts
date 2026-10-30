import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

let webserver_port = 8000;
const configPath = path.resolve(__dirname, '../../../sites/common_site_config.json');

if (fs.existsSync(configPath)) {
	try {
		const common_site_config = JSON.parse(fs.readFileSync(configPath, 'utf-8'));
		if (common_site_config.webserver_port) {
			webserver_port = common_site_config.webserver_port;
		}
	} catch {
		// use default port
	}
}

export default {
	'^/(app|api|assets|files|private)': {
		target: `http://127.0.0.1:${webserver_port}`,
		ws: true,
		router: function(req: any) {
			const site_name = req.headers.host.split(':')[0];
			return `http://${site_name}:${webserver_port}`;
		}
	}
};

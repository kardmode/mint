import { useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { FrappeProvider } from 'frappe-react-sdk'
import { Toaster } from './components/ui/sonner'
import { ThemeProvider } from './components/ui/theme-provider'
import BankStatementImporter from './pages/BankStatementImporter'
import BankReconciliation from './pages/BankReconciliation'

function App() {
	useEffect(() => {
		// Apply dynamic RTL / LTR layout direction based on Frappe session
		const boot = (window as any).frappe?.boot
		const layoutDir = boot?.layout_direction || 
			(['ar', 'he', 'fa', 'ur'].includes(boot?.lang) ? 'rtl' : 'ltr')
		document.documentElement.setAttribute('dir', layoutDir)
		if (boot?.lang) {
			document.documentElement.setAttribute('lang', boot.lang)
		}

		// Check if user is logged in by checking the Cookie "user_id"
		const userId = document.cookie?.split('; ').find(row => row.startsWith('user_id='))?.split('=')[1]?.trim()
		const isLoggedIn = userId !== 'Guest'

		if (!isLoggedIn) {
			if (import.meta.env.DEV) {
				return
			}
			// Redirect to Frappe login page
			window.location.href = '/login?redirect-to=/mint'
			return
		}
	}, [])

	return (
		<FrappeProvider
			swrConfig={{
				errorRetryCount: 2
			}}
			socketPort={import.meta.env.VITE_SOCKET_PORT}
			siteName={(window as any).frappe?.boot?.sitename ?? import.meta.env.VITE_SITE_NAME}>
			<ThemeProvider
				defaultTheme={(window as any).frappe?.boot?.desk_theme ?? "Automatic"}
			>
				{(window as any).frappe?.boot?.user?.name && (window as any).frappe?.boot?.user?.name !== 'Guest' &&
					<BrowserRouter basename={import.meta.env.VITE_BASE_NAME ? `/${import.meta.env.VITE_BASE_NAME}` : ''}>
						<Routes>
							<Route index element={<BankReconciliation />} />
							<Route path="/statement-importer" element={<BankStatementImporter />} />
							<Route path="*" element={<Navigate to="/" />} />
						</Routes>
					</BrowserRouter>
				}
				<Toaster richColors />
			</ThemeProvider>
		</FrappeProvider>
	)
}

export default App

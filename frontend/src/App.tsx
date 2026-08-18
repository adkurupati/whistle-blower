import { BrowserRouter, Route, Routes } from 'react-router-dom'

import AppLayout from '@/layouts/AppLayout'
import Dashboard from '@/pages/Dashboard'
import GameDetail from '@/pages/GameDetail'
import RefereeDetail from '@/pages/RefereeDetail'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppLayout />}>
          <Route index element={<Dashboard />} />
          <Route path="games/:gameId" element={<GameDetail />} />
          <Route path="referees/:refereeId" element={<RefereeDetail />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

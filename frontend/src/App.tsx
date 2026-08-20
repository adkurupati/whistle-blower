import { BrowserRouter, Route, Routes } from 'react-router-dom'

import AppLayout from '@/layouts/AppLayout'
import Dashboard from '@/pages/Dashboard'
import GameDetail from '@/pages/GameDetail'
import Login from '@/pages/Login'
import RefereeDetail from '@/pages/RefereeDetail'
import Signup from '@/pages/Signup'
import Teams from '@/pages/Teams'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppLayout />}>
          <Route index element={<Dashboard />} />
          <Route path="teams" element={<Teams />} />
          <Route path="games/:gameId" element={<GameDetail />} />
          <Route path="referees/:refereeId" element={<RefereeDetail />} />
          <Route path="login" element={<Login />} />
          <Route path="signup" element={<Signup />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

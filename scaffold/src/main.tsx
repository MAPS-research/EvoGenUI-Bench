import React from 'react'
import ReactDOM from 'react-dom/client'

import * as AppModule from './App'
import './styles.css'

type AppModuleShape = {
  default?: React.ComponentType
  App?: React.ComponentType
}

const resolvedModule = AppModule as AppModuleShape
const App = resolvedModule.default ?? resolvedModule.App ?? (() => null)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)

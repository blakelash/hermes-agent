import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import { type CommandCenterSection } from '@/app/command-center'
import {
  AGENTS_ROUTE,
  appViewForPath,
  COMMAND_CENTER_ROUTE,
  DASHBOARD_ROUTE,
  isOverlayView,
  NEW_CHAT_ROUTE,
  WORKSPACE_ROUTE
} from '@/app/routes'

const SECTIONS = ['sessions', 'system', 'usage'] as const

export function useOverlayRouting() {
  const location = useLocation()
  const navigate = useNavigate()

  const currentView = appViewForPath(location.pathname)
  const settingsOpen = currentView === 'settings'
  const commandCenterOpen = currentView === 'command-center'
  const agentsOpen = currentView === 'agents'
  const cronOpen = currentView === 'cron'
  const profilesOpen = currentView === 'profiles'
  const dashboardOpen = currentView === 'dashboard'
  const workspaceOpen = currentView === 'workspace'
  const chatOpen = currentView === 'chat'
  const overlayOpen = isOverlayView(currentView)

  // Overlay routes (settings/command-center/agents) stash the underlying path
  // so closing them returns there instead of bouncing to /.
  const returnPathRef = useRef(NEW_CHAT_ROUTE)

  useEffect(() => {
    if (!overlayOpen) {
      returnPathRef.current = `${location.pathname}${location.search}${location.hash}`
    }
  }, [location.hash, location.pathname, location.search, overlayOpen])

  const commandCenterInitialSection = useMemo<CommandCenterSection | undefined>(
    () => SECTIONS.find(value => value === new URLSearchParams(location.search).get('section')),
    [location.search]
  )

  const openCommandCenterSection = useCallback(
    (section: CommandCenterSection) => navigate(`${COMMAND_CENTER_ROUTE}?section=${section}`),
    [navigate]
  )

  const closeOverlayToPreviousRoute = useCallback(
    () => navigate(returnPathRef.current || NEW_CHAT_ROUTE, { replace: true }),
    [navigate]
  )

  const toggleCommandCenter = useCallback(() => {
    if (commandCenterOpen) {
      closeOverlayToPreviousRoute()
    } else {
      navigate(COMMAND_CENTER_ROUTE)
    }
  }, [closeOverlayToPreviousRoute, commandCenterOpen, navigate])

  const openAgents = useCallback(() => navigate(AGENTS_ROUTE), [navigate])
  const openDashboard = useCallback(() => navigate(DASHBOARD_ROUTE), [navigate])
  const openWorkspace = useCallback(() => navigate(WORKSPACE_ROUTE), [navigate])

  return {
    agentsOpen,
    chatOpen,
    closeOverlayToPreviousRoute,
    commandCenterInitialSection,
    commandCenterOpen,
    cronOpen,
    currentView,
    dashboardOpen,
    openAgents,
    openCommandCenterSection,
    openDashboard,
    openWorkspace,
    profilesOpen,
    settingsOpen,
    toggleCommandCenter,
    workspaceOpen
  }
}

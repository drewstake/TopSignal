// src/components/Dashboard/Dashboard.tsx
import React from 'react';
import { useDashboardLogic } from './useDashboardLogic';
import { DashboardUI }        from './DashboardUI';

export default function Dashboard() {
  const props = useDashboardLogic();
  return <DashboardUI {...props} />;
}

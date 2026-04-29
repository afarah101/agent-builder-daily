import { exec } from 'child_process';
import { promisify } from 'util';

const execAsync = promisify(exec);

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  try {
    const { stdout, stderr } = await execAsync('python3 orchestrator.py', {
      cwd: process.env.VERCEL_PROJECT_ROOT || '/var/task',
      timeout: 300000 // 5 min timeout
    });

    return res.status(200).json({
      success: true,
      message: 'Scout run completed',
      stdout,
      stderr
    });
  } catch (error) {
    return res.status(500).json({
      success: false,
      error: error.message,
      stderr: error.stderr
    });
  }
}

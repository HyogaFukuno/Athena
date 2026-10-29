package net.minecraft.server;
import java.io.File;
import java.io.IOException;
import java.lang.management.ManagementFactory;
import java.nio.file.Files;
import com.sun.management.UnixOperatingSystemMXBean;
public final class RegionOpenFailureProbe {
 public static void main(String[] args) throws Exception {
  // Force the OS read-failure path without changing any actual template files.
  var directory = Files.createTempDirectory("athena-region-read-fault-");
  File file = new File(directory.toString()) {
   @Override public boolean isFile() { return true; }
  };
  var os = (UnixOperatingSystemMXBean)ManagementFactory.getOperatingSystemMXBean();
  for(int i=0;i<5;i++)try{AthenaTemplateRegion.open(file);}catch(IOException expected){}
  long before=os.getOpenFileDescriptorCount(); int failures=0;
  for(int i=0;i<100;i++)try{AthenaTemplateRegion.open(file);}catch(IOException expected){failures++;}
  long after=os.getOpenFileDescriptorCount();
  System.out.println("injectedReadFailures="+failures+" fdBefore="+before+" fdAfter="+after+" fdDelta="+(after-before));
  Files.delete(directory);
 }
}

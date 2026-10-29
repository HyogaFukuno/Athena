package net.minecraft.server;
public class RegistryCapacityProbe {
 public static void main(String[] args) throws Exception {
  DispenserRegistry.register();
  var field=RegistryID.class.getDeclaredField("objects");field.setAccessible(true);
  Object[] slots=(Object[])field.get(Block.BLOCK_STATE_IDS);
  int highest=-1,count=0;
  for(int i=0;i<slots.length;i++)if(slots[i]!=null){highest=i;count++;}
  System.out.println("capacity="+slots.length+", highestId="+highest+", nonNullStates="+count);
 }
}
